/*
 *  Perpetua - open-source and cross-platform KVM software.
 *  Copyright (c) 2026 Federico Izzi.
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 * Probe helper: print the cursor position mutter attaches to a ScreenCast
 * stream (cursor_mode=METADATA), one line per buffer that carries the meta:
 *
 *     <CLOCK_MONOTONIC us> <x> <y> <valid>
 *
 * valid=0 is a meta with an invalid cursor id (cursor off this stream); x/y are
 * printed anyway so a multi-monitor probe can see what mutter reports there.
 * At exit stderr gets the buffer counts, how many were cursor-only (no new
 * video data), and the helper's own CPU time.
 *
 * Buffers without cursor metadata, or with an invalid one (cursor off this
 * stream), are counted separately on stderr at exit.
 *
 *     probe_pw_cursor <pipewire-remote-fd> <node-id> [max-fps]
 *
 * max-fps caps the negotiated video framerate (default 1000): whether cursor
 * metadata keeps flowing past that cap is what the probe measures.
 *
 * Build: cc -O2 -o probe_pw_cursor probe_pw_cursor.c $(pkg-config --cflags --libs libpipewire-0.3)
 */
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>

#include <pipewire/pipewire.h>
#include <spa/buffer/meta.h>
#include <spa/param/video/format-utils.h>
#include <spa/pod/builder.h>

#define CURSOR_META_SIZE(w, h) \
    (sizeof(struct spa_meta_cursor) + sizeof(struct spa_meta_bitmap) + (w) * (h) * 4)

struct data {
    struct pw_main_loop *loop;
    struct pw_stream *stream;
    struct spa_hook listener;
    unsigned long with_cursor;
    unsigned long invalid_cursor;
    unsigned long no_meta;
    unsigned long cursor_only;
    unsigned long frames;
};

static long long now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000000LL + ts.tv_nsec / 1000;
}

static void on_process(void *userdata) {
    struct data *d = userdata;
    struct pw_buffer *b;

    while ((b = pw_stream_dequeue_buffer(d->stream)) != NULL) {
        struct spa_meta_cursor *mc = spa_buffer_find_meta_data(
            b->buffer, SPA_META_Cursor, sizeof(*mc));
        struct spa_chunk *chunk = b->buffer->n_datas ? b->buffer->datas[0].chunk : NULL;
        if (chunk == NULL || chunk->size == 0 || (chunk->flags & SPA_CHUNK_FLAG_CORRUPTED))
            d->cursor_only++;
        else
            d->frames++;
        if (mc == NULL) {
            d->no_meta++;
        } else {
            int valid = spa_meta_cursor_is_valid(mc);
            if (valid)
                d->with_cursor++;
            else
                d->invalid_cursor++;
            printf("%lld %d %d %d\n", now_us(), mc->position.x, mc->position.y, valid);
            fflush(stdout);
        }
        pw_stream_queue_buffer(d->stream, b);
    }
}

static void on_param_changed(void *userdata, uint32_t id, const struct spa_pod *param) {
    struct data *d = userdata;
    if (param == NULL || id != SPA_PARAM_Format)
        return;

    uint8_t buffer[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
    const struct spa_pod *params[2];
    params[0] = spa_pod_builder_add_object(
        &b, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
        SPA_PARAM_META_type, SPA_POD_Id(SPA_META_Cursor),
        SPA_PARAM_META_size,
        /* mutter offers a fixed size for a 384x384 bitmap; the range must
         * reach it or the meta is silently not negotiated. */
        SPA_POD_CHOICE_RANGE_Int(CURSOR_META_SIZE(64, 64), CURSOR_META_SIZE(1, 1),
                                 CURSOR_META_SIZE(1024, 1024)));
    params[1] = spa_pod_builder_add_object(
        &b, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers,
        SPA_PARAM_BUFFERS_dataType,
        SPA_POD_CHOICE_FLAGS_Int((1 << SPA_DATA_MemPtr) | (1 << SPA_DATA_MemFd)));
    pw_stream_update_params(d->stream, params, 2);
}

static void on_state_changed(void *userdata, enum pw_stream_state old,
                             enum pw_stream_state state, const char *error) {
    (void)userdata;
    (void)old;
    fprintf(stderr, "stream state: %s%s%s\n", pw_stream_state_as_string(state),
            error ? " error=" : "", error ? error : "");
}

static const struct pw_stream_events stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = on_state_changed,
    .param_changed = on_param_changed,
    .process = on_process,
};

static void do_quit(void *userdata, int signal_number) {
    (void)signal_number;
    struct data *d = userdata;
    pw_main_loop_quit(d->loop);
}

int main(int argc, char *argv[]) {
    if (argc != 3 && argc != 4) {
        fprintf(stderr, "usage: %s <pipewire-fd> <node-id> [max-fps]\n", argv[0]);
        return 2;
    }
    int fd = atoi(argv[1]);
    uint32_t node_id = (uint32_t)strtoul(argv[2], NULL, 10);
    uint32_t max_fps = argc == 4 ? (uint32_t)strtoul(argv[3], NULL, 10) : 1000;
    if (max_fps == 0)
        max_fps = 1;

    struct data d = {0};
    pw_init(&argc, &argv);
    d.loop = pw_main_loop_new(NULL);
    pw_loop_add_signal(pw_main_loop_get_loop(d.loop), SIGINT, do_quit, &d);
    pw_loop_add_signal(pw_main_loop_get_loop(d.loop), SIGTERM, do_quit, &d);

    struct pw_context *context = pw_context_new(pw_main_loop_get_loop(d.loop), NULL, 0);
    struct pw_core *core = pw_context_connect_fd(context, fd, NULL, 0);
    if (core == NULL) {
        fprintf(stderr, "pw_context_connect_fd failed: %s\n", strerror(errno));
        return 1;
    }

    d.stream = pw_stream_new(core, "perpetua-cursor-probe",
                             pw_properties_new(PW_KEY_MEDIA_TYPE, "Video",
                                               PW_KEY_MEDIA_CATEGORY, "Capture",
                                               PW_KEY_MEDIA_ROLE, "Screen", NULL));
    pw_stream_add_listener(d.stream, &d.listener, &stream_events, &d);

    uint8_t buffer[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
    const struct spa_pod *params[1];
    params[0] = spa_pod_builder_add_object(
        &b, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format,
        SPA_POD_CHOICE_ENUM_Id(5, SPA_VIDEO_FORMAT_BGRx, SPA_VIDEO_FORMAT_BGRx,
                               SPA_VIDEO_FORMAT_BGRA, SPA_VIDEO_FORMAT_RGBx,
                               SPA_VIDEO_FORMAT_RGBA),
        SPA_FORMAT_VIDEO_size,
        SPA_POD_CHOICE_RANGE_Rectangle(&SPA_RECTANGLE(320, 240), &SPA_RECTANGLE(1, 1),
                                       &SPA_RECTANGLE(8192, 8192)),
        SPA_FORMAT_VIDEO_framerate,
        SPA_POD_CHOICE_RANGE_Fraction(&SPA_FRACTION(0, 1), &SPA_FRACTION(0, 1),
                                      &SPA_FRACTION(max_fps, 1)),
        SPA_FORMAT_VIDEO_maxFramerate,
        SPA_POD_CHOICE_RANGE_Fraction(&SPA_FRACTION(max_fps, 1), &SPA_FRACTION(0, 1),
                                      &SPA_FRACTION(max_fps, 1)));

    if (pw_stream_connect(d.stream, PW_DIRECTION_INPUT, node_id,
                          PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS,
                          params, 1) < 0) {
        fprintf(stderr, "pw_stream_connect failed\n");
        return 1;
    }

    pw_main_loop_run(d.loop);
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    fprintf(stderr,
            "buffers: with_cursor=%lu invalid_cursor=%lu no_meta=%lu "
            "cursor_only=%lu frames=%lu cpu_user_ms=%ld cpu_sys_ms=%ld\n",
            d.with_cursor, d.invalid_cursor, d.no_meta, d.cursor_only, d.frames,
            ru.ru_utime.tv_sec * 1000 + ru.ru_utime.tv_usec / 1000,
            ru.ru_stime.tv_sec * 1000 + ru.ru_stime.tv_usec / 1000);

    pw_stream_destroy(d.stream);
    pw_core_disconnect(core);
    pw_context_destroy(context);
    pw_main_loop_destroy(d.loop);
    pw_deinit();
    return 0;
}
