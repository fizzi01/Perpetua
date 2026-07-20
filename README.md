<div align="center">
    <img src="src-gui/src-tauri/icons/icon.png" alt="Perpetua Logo" width="128" height="128">
    <h1>Perpetua</h1>

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=flat-square)](https://www.gnu.org/licenses/gpl-3.0)
[![GitHub release](https://img.shields.io/github/v/release/fizzi01/Perpetua?style=flat-square&color=%234f47e4)](https://github.com/fizzi01/Perpetua/releases/latest)
[![GitHub Downloads (all assets, latest release)](https://img.shields.io/github/downloads/fizzi01/Perpetua/latest/total?style=flat-square&logo=github&label=Downloads&color=%234f47e4)](https://github.com/fizzi01/Perpetua/releases/latest)

</div>

Perpetua is an open-source, cross-platform KVM software that lets you share a single keyboard and mouse across multiple devices. Inspired by Apple's Universal Control, it provides seamless cursor movement between devices, keyboard sharing, and automatic clipboard synchronization. All secured with TLS encryption.

Built with Python using uvloop (macOS/Linux) and winloop (Windows) as event loops and compiled with Nuitka, for low-latency and responsive input handling. This results in very high performance with just ~6% CPU usage under heavy load.


<div align="center">
    <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/imgs/dark.png?raw=true">
        <source media="(prefers-color-scheme: light)" srcset="docs/imgs/light.png?raw=true">
        <img alt="Perpetua Server View" srcset="docs/imgs/dark.png" width="450">
    </picture>
</div>


## Table of Contents

- [Getting Started](#getting-started)
- [Platform Support](#platform-support)
- [Multi-monitor](#multi-monitor--layout)
- [Usage](#usage)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Development / Building from Source](#development--building-from-source)
- [Roadmap](#roadmap)
- [License](#license)


## Getting Started

[![GitHub Downloads (all assets, latest release)](https://img.shields.io/github/downloads/fizzi01/Perpetua/latest/total?style=for-the-badge&logo=github&label=DOWNLOAD%20LATEST&color=%234f47e4)](https://github.com/fizzi01/Perpetua/releases/latest)

- **macOS**: Extract the `.zip`, then launch `Perpetua.app`. Clear the quarantine with `xattr -c Perpetua.app` before launching.
- **Windows**: Extract the archive and run `Perpetua.exe` inside the `Perpetua` folder.
- **Linux** (x86_64 / aarch64), pick one:
  - **Debian / Ubuntu**: `sudo dpkg -i perpetua_*.deb` (or `sudo apt install ./perpetua_*.deb`).
  - **Fedora / RHEL / openSUSE**: `sudo dnf install perpetua-*.rpm` (or `sudo rpm -i`).
  - **Arch Linux**: install `perpetua-bin` from the AUR (see [`scripts/aur/PKGBUILD`](scripts/aur/PKGBUILD)).
  - **Any distro**: download the `Perpetua-*.AppImage`, `chmod +x`, and run it. The AppImage doesn't install udev rules, run [`scripts/enable_uinput.sh`](scripts/enable_uinput.sh) once with `sudo` for keyboard input to work.

  Baseline: `glibc >= 2.39` (Ubuntu 24.04, Fedora 40, Debian 13, Arch). The `libei` and `liboeffis` runtime libs are required for Wayland InputCapture on GNOME/KDE; the `.deb` declares them as `Depends:` / `Recommends:`, the `.rpm` mirrors this.

The GUI will guide you through choosing server or client mode and the initial configuration.

> [!NOTE]
> **macOS:** Perpetua requires Accessibility permissions and Local Network access (Privacy & Security). At first launch, macOS will show prompts to grant these permissions. You can also manage permissions manually in System Settings > Privacy & Security.

### First-Time Setup

Install Perpetua on both machines, then run through these steps once. After this, every reconnection is automatic.

`Server` is the machine with the physical keyboard and mouse you want to share. `Client` is the machine you want to control with them.

1. On the machine that owns the keyboard and mouse, open Perpetua and pick `Server`. Press the power button to start it.
2. On the other machine, open Perpetua and pick `Client`. Press the power button. The client auto-discovers the server on the local network.
3. Pair the two machines using the one-time code shown on the server. The full flow (OTP, Allow/Deny, manual pre-registration) is described in [First Connection and OTP Pairing](#configuration).
4. After approval, the server opens the **Layout Editor**: drag each client monitor next to your server monitors and press **Save**. Details and edge cases in [Multi-monitor & layout](#multi-monitor--layout).
5. Done!

> [!TIP]
> You can pre-register clients in `Server > Clients` to skip the Allow/Deny prompt entirely. See [Server Configuration](#configuration).

> [!NOTE]
> If the cursor gets stuck on the wrong machine, press `Ctrl + Shift + Q` on the server to force-quit Perpetua. Full hotkey list in [Keyboard Shortcuts](#keyboard-shortcuts).


## Platform Support

### Server (controls other machines)

| Feature | macOS | Windows | X11 |             Wayland (GNOME)             |   Wayland (KDE)    |  Wayland (Others)   |
|---|:---:|:---:|:---:|:---------------------------------------:|:------------------:|:-------------------:|
| Mouse capture | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: |           :heavy_check_mark:            | :heavy_check_mark: |         :x:         |
| Keyboard capture | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: |           :heavy_check_mark:            | :heavy_check_mark: | :heavy_check_mark:  |
| Clipboard sync | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark:  | :heavy_check_mark: |  :heavy_check_mark: |

### Client (controlled by a server)

| Feature | macOS | Windows | X11 | Wayland (GNOME) | Wayland (KDE) | Wayland (Others) |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Mouse control | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :x: |
| Keyboard control | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: |
| Clipboard sync | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: | :heavy_check_mark: |

Supported desktop environments: **GNOME >= 45** or **KDE Plasma >= 6.1**.

Other Wayland compositors (wlroots-based, Hyprland, Sway, etc.) are not yet supported.

> [!NOTE]
> **Linux (Wayland):** requires `libei` and `liboeffis` installed on the system.

### Known Issues

> [!Important]
> - **Windows**: You can't control a Windows client if there is no real mouse connected to the machine.
>
> - **Input Capture Conflicts**: Perpetua cannot control the mouse when other applications have exclusive input capture (e.g., video games). This is an architectural limitation.


## Multi-monitor & layout

Perpetua supports multi-monitor setups on both the server and the client. Instead of saying "the client is on the right", you tell Perpetua exactly **where each client monitor sits** next to your server. This lets you arrange a laptop with an external display, two laptops side by side, or any mix you like.

### The Layout Editor

After a client is approved (or any time you open `Server > Clients > <client> > Layout`), the server shows a grid with your server monitors on one side and the client's monitors on the other. **Drag each client monitor to where you want the cursor to enter that screen**, then press **Save**. Subsequent reconnections of the same client skip the editor and reuse the saved layout.

> [!TIP]
> At least one client monitor must touch a server monitor (sharing an edge). Without that, the cursor has no way to cross over. The editor refuses to save a layout that breaks this rule, and explains why.

### Hot-plug

Plugging or unplugging a display on either machine is detected automatically, no restart needed. If a monitor disappears, the placements that pointed to it become **orphaned**: the GUI marks them so you can either remove them or drag them somewhere valid. The cursor keeps working for the monitors that are still there.

### Backwards compatibility

Older configurations that use a single `screen_position` (`top` / `bottom` / `left` / `right`) keep working as a fallback when a client has no explicit `placements`. The first time you open the Layout Editor for a legacy client and save, the configuration is upgraded automatically.

If you prefer to skip the GUI and edit the JSON config file directly, see [Manual placements](#manual-placements) in the Configuration section.


## Usage

### Background Mode

You can run Perpetua as a background service using the daemon mode:

```bash
# Run in daemon mode
Perpetua --daemon

# Automatically start as server
Perpetua --daemon --server

# Automatically start as client
Perpetua --daemon --client
```

For a full list of available commands and options:
```bash
Perpetua --help
```

### Keyboard Shortcuts

The following hotkeys are available on the **server** machine to control input focus without moving the mouse to a screen edge.

| Shortcut | Action |
|---|---|
| `Ctrl + Shift + P + ←` | Switch focus to the **left** client |
| `Ctrl + Shift + P + →` | Switch focus to the **right** client |
| `Ctrl + Shift + P + ↑` | Switch focus to the **top** client |
| `Ctrl + Shift + P + ↓` | Switch focus to the **bottom** client |
| `Ctrl + Shift + P + 1` / `2` | **Cycle** through connected clients (prev / next) |
| `Ctrl + Shift + P + Esc` | Return focus to the **server** |
| `Ctrl + Shift + Q` | **Panic** - force-quit Perpetua |

> [!NOTE]
> Client switch hotkeys require the server to be running and at least one client to be connected. The cycle shortcut is useful in multi-monitor setups where direction-based switching becomes ambiguous (e.g. two clients placed on the same edge).


## Configuration

Perpetua uses JSON to define client and server settings. The configuration file is automatically generated on first launch with sensible defaults, requiring minimal manual intervention for most use cases.

<details>
<summary><b>Server Configuration</b></summary>

Basic server setup (certificates, network binding) is handled automatically. To accept client connections you have two options:

- **Let the GUI handle it**: when a new client tries to connect, the `Server` shows an Allow/Deny card. Approving opens the Layout Editor. The full flow is described in [First Connection and OTP Pairing](#first-connection-and-otp-pairing).
- **Pre-register each client manually** in `Server > Clients` (or by editing the config file). Pre-registered clients skip the Allow/Deny prompt. For each entry specify the client's IPs and/or hostname, plus either a legacy `screen_position` (`left`, `right`, `top`, `bottom`) or a list of `placements` (recommended for multi-monitor setups, see [Manual placements](#manual-placements)).

</details>

<details>
<summary><b>Client Configuration</b></summary>

Clients can find servers in two ways:

Auto Discovery (Default):
- Scans the local network for available servers
- Works out of the box, no configuration needed

Manual Configuration:
- Set the server's hostname or IP address directly in the config file (or in the appropriate field in `Client > Options`)
- Use this when auto-discovery doesn't work or you have a static network setup

</details>

<a id="first-connection-and-otp-pairing"></a>
<details>
<summary><b>First Connection and OTP Pairing</b></summary>

When a client connects to a new server for the first time, it needs to get the server's TLS certificate to establish a secure connection. Here's how it works:

1. The `Client` starts the connection process and signals the `Server` it wants to pair ("*Secure connection*" must be **enabled**, it is by default).
2. The `Server` generates an OTP automatically and shows it on the GUI under the power button.
3. Share the OTP with the user of the `Client` and enter it when prompted.
4. The `Server` shows an Allow/Deny card. Picking **Allow** adds the client to the allowlist and opens the **Layout Editor** to position the client's monitors; picking Deny rejects the handshake.
5. Done!

You can also generate the OTP manually from the `Security` section on the `Server` (the same card appears under the power button). This is useful when pre-registering clients without waiting for an incoming request.

The OTP is just for the initial certificate exchange. After that, connections to the same server authenticate automatically using the saved certificates. The OTP itself never travels over the network - it is shown only on the server's screen.

</details>

<a id="configuration-file-structure"></a>
<details>
<summary><b>Configuration File Structure</b></summary>

The configuration file lives at:
- macOS: `$HOME/Library/Caches/Perpetua`
- Windows: `%LOCALAPPDATA%\Perpetua`
- Linux: `$XDG_CONFIG_HOME/perpetua` (default `$HOME/.config/perpetua`); the daemon also writes log and runtime files under `$XDG_STATE_HOME/perpetua` and `$XDG_RUNTIME_DIR/perpetua`. A legacy `~/.perpetua` directory is migrated on first launch.

The configuration [json file](#file-structure) is split into three sections: `server`, `client`, and `general`.

#### Server Section

`streams_enabled` controls what the server will manage on each connected client:
- `1`: Mouse
- `4`: Keyboard
- `12`: Clipboard

`log_level` sets the logging verbosity:
- `0`: Debug (detailed logs)
- `1`: Info (standard logs)

`pairing_port` is the port used for the initial OTP-based certificate exchange. Leave it `null` to derive it as `port - 2`. The value is advertised over mDNS so clients discover it automatically.

`authorized_clients` lists the clients that can connect. To add a new client, you only need to specify:
- `uid`: Unique identifier
- `host_name` and/or `ip_addresses`: Client's network identity
- `screen_position`: Where the client is positioned relative to the server (`left`, `right`, `top`, `bottom`)

Other fields are automatically populated by the system. Clients can also be added on the fly from the GUI via the Allow/Deny prompt.

#### Client Section

The same field names have the same meaning as in the server section. The `server_info` block tells the client which server to connect to (leave it empty for auto-discovery or fill in the `host` field for manual configuration).

#### General Section

These parameters affect the application's internal behavior. Only modify them if you know what you're doing.

#### File Structure
```json
{
    "server": {
        "uid": "...",
        "host": "0.0.0.0",
        "port": 55655,
        "pairing_port": null,
        "heartbeat_interval": 1,
        "streams_enabled": {
            "1": true,
            "4": true,
            "12": true
        },
        "ssl_enabled": true,
        "log_level": 1,
        "authorized_clients": [
            {
                "uid": "...",
                "host_name": "MYCLIENT",
                "ip_addresses": [
                    "192.168.1.66"
                ],
                "first_connection_date": "2026-02-02 19:09:00",
                "last_connection_date": "2026-02-02 19:16:12",
                "screen_position": "top",
                "screen_resolution": "1920x1080",
                "placements": [
                    {
                        "client_monitor_id": 0,
                        "workspace_x": 1920,
                        "workspace_y": 0,
                        "width": 1920,
                        "height": 1080
                    }
                ],
                "ssl": true,
                "is_connected": true,
                "additional_params": {}
            }
        ]
    },
    "client": {
        "server_info": {
            "uid": "",
            "host": "",
            "hostname": null,
            "port": 55655,
            "heartbeat_interval": 1,
            "auto_reconnect": true,
            "ssl": true,
            "additional_params": {}
        },
        "uid": "...",
        "client_hostname": "MYSERVER",
        "streams_enabled": {
            "1": true,
            "4": true,
            "12": true
        },
        "ssl_enabled": true,
        "log_level": 1
    },
    "general": {
        "default_host": "0.0.0.0",
        "default_port": 55655,
        "default_daemon_port": 55652
    }
}
```

</details>

<a id="manual-placements"></a>
<details>
<summary><b>Manual placements (multi-monitor)</b></summary>

> If you're using the GUI's Layout Editor (described in [Multi-monitor & layout](#multi-monitor--layout)) you can skip this section, since the editor writes the same JSON for you. Read on only if you want to edit the config file by hand or prepare a configuration before the client ever connects.

#### The idea, in one paragraph

Imagine all your screens laid out on a giant virtual canvas. The server's own monitors sit at fixed positions on this canvas (top-left of the primary monitor is `(0, 0)`, just like the OS does). A **placement** is a rectangle on the same canvas that says: "client monitor X is positioned **here**". When the cursor walks off the edge of a server monitor and finds a placement on the other side, it crosses over to that client monitor.

That's it. Everything below is just how to write that idea down in the config file.

#### Anatomy of a placement

A placement is a JSON object with five fields, living inside a client's entry in `authorized_clients` (see the example in [Configuration File Structure](#configuration-file-structure) above):

```json
{
  "client_monitor_id": 0,
  "workspace_x": 1920,
  "workspace_y": 0,
  "width": 1920,
  "height": 1080
}
```

| Field | What it means |
|---|---|
| `client_monitor_id` | The ID the **client's OS** assigned to one of its monitors. The client reports its monitors at connect time, and the IDs show up in the GUI's Layout Editor (or, if you must, in the client's logs at INFO level). If in doubt, start with `0` (that's almost always the primary). |
| `workspace_x` | Where the **left edge** of this client monitor sits on the canvas (pixels). |
| `workspace_y` | Where the **top edge** of this client monitor sits on the canvas (pixels). |
| `width` | How wide the placement rectangle is (pixels). Usually the client monitor's actual width. |
| `height` | How tall the placement rectangle is (pixels). Usually the client monitor's actual height. |

#### Rules the server enforces

1. A placement can't **overlap** any server monitor.
2. A placement can't **overlap** another client's placement (or another of its own placements).
3. Every placement must **touch** a server monitor on at least one edge, either directly or through a chain of placements on the same client. The cursor needs a way home.
4. `width` and `height` must be greater than zero.

If any rule fails, the save is rejected with a clear error message. Nothing is half-written.

#### A worked example

Setup:
- The **server** has a single 1920×1080 monitor. Its canvas goes from `(0, 0)` to `(1920, 1080)`.
- The **client** is a laptop with two monitors: the built-in screen (1920×1080, monitor id `0`) and an external (2560×1440, monitor id `1`).
- You want the laptop's built-in screen **to the right** of the server, and the external **to the right of the laptop**.

The `placements` block, inside that client's entry in `authorized_clients`:

```json
"placements": [
  {
    "client_monitor_id": 0,
    "workspace_x": 1920,
    "workspace_y": 0,
    "width": 1920,
    "height": 1080
  },
  {
    "client_monitor_id": 1,
    "workspace_x": 3840,
    "workspace_y": 0,
    "width": 2560,
    "height": 1440
  }
]
```

Reading the canvas left-to-right:
- `0 → 1920` is your server monitor.
- `1920 → 3840` is the client's built-in screen. Its left edge touches the server's right edge → moving the cursor right off the server enters this monitor.
- `3840 → 6400` is the external. Its left edge touches the laptop's right edge → moving the cursor right off the laptop enters the external. From here, going left walks back through the laptop and then back to the server.

#### Tips

- **Single monitor**: write one placement that touches the chosen server edge (left, right, top or bottom). Use the client monitor's native width and height.
- **Stacking vertically**: change `workspace_y` instead of `workspace_x`.
- **Two clients on the same side**: allowed, as long as their placements don't overlap. Put them at different `workspace_y` values, or one above the other. If two placements end up sharing the exact same entry edge, the server picks the first one and logs a warning. Just move one of them to disambiguate.
- **Easiest way**: open the Layout Editor in the GUI, drag the boxes where you want them, then look at the JSON the editor saved. Copy it as a starting point.

</details>


## Troubleshooting

<details>
<summary><b>The client doesn't show up on the server</b></summary>

Auto-discovery uses mDNS. Make sure:

- The `Server` is running and `Secure connection` is enabled.
- Both machines are on the same LAN/subnet.
- UDP `5353` is not blocked by a firewall (see [Firewall ports](#firewall-ports)).

As a fallback, set the server's hostname or IP directly in the `Client > Options` section.

</details>

<details>
<summary><b>OTP card never appears on the server</b></summary>

The OTP only appears when the `Client` actively asks to pair. Check that:

- `Secure connection` is enabled on the `Server` (it is by default).
- The pairing port on the server (default `port - 2`) is reachable from the client (see [Firewall ports](#firewall-ports)).
- The client is actually trying to connect - its power button is green and it has discovered the server.

You can also generate the OTP manually from the `Security` section on the `Server`.

</details>

<details>
<summary><b>Cursor doesn't cross to the other screen</b></summary>

Check the client's screen position in `Server > Clients`. The position (top, bottom, left, right) defines which edge of the server screen the cursor uses to enter the client. If the client is offline, the cursor stays on the server side.

If the cursor gets stuck on the wrong machine, press `Ctrl + Shift + Q` on the server to force-quit Perpetua.

</details>

<details>
<summary><b>Cursor warps to the wrong client monitor (or to no client at all)</b></summary>

Open `Server > Layout` and verify that each client monitor sits adjacent to a server monitor; or to another client monitor that itself reaches the server.

After a hot-plug, placements pointing to a disconnected monitor are flagged as **orphan**. Drag them back into a valid position or delete them.

If two clients share the exact same server edge, the server picks the first one and logs a warning. Reposition one of them in the Layout Editor to disambiguate.

</details>

<details>
<summary><b>"Port already in use" on server start</b></summary>

The configured TCP `port` (default `55655`) is occupied by another process. Open `Server > Options` and pick a different value, then retry.

</details>

<details>
<summary><b>Pairing port collision</b></summary>

The pairing listener (default `port - 2`) auto-falls-back to the next free adjacent port when busy. The actual port is advertised over mDNS so clients pick it up automatically.

To pin a specific port, set `pairing_port` in the server config.

</details>

<details>
<summary><b>GUI can't reach the daemon</b></summary>

The daemon writes its endpoint to `<config-dir>/runtime/daemon.endpoint`, and the GUI reads it on startup. You can override it with the `PERPETUA_DAEMON_ENDPOINT` environment variable:

```bash
# Linux / macOS
PERPETUA_DAEMON_ENDPOINT=unix:///tmp/my-perpetua.sock Perpetua

# Windows
PERPETUA_DAEMON_ENDPOINT=tcp://127.0.0.1:55700 Perpetua
```

Stale endpoint files left behind by a crash are harmless. The GUI falls back to the platform default and the daemon rewrites the file on next start.

</details>

<a id="firewall-ports"></a>
<details>
<summary><b>Firewall ports</b></summary>

For a LAN-only setup only the `Server` needs inbound rules:

- TCP `55655` (`port`): main TLS data channel.
- TCP `55653` (`pairing_port`): plaintext OTP-based pairing.
- UDP `5353`: mDNS auto-discovery.

The `Client` only makes outbound connections.

</details>


## Development / Building from Source

This section is for contributors and people building from source. End users can grab the prebuilt binaries from [Releases](https://github.com/fizzi01/Perpetua/releases/latest).

<details>
<summary><b>Prerequisites</b></summary>

- Python Environment:
    - Python 3.11 or 3.12
    - Poetry


- GUI Framework:
    - Node.js
    - Rust

</details>
<details>
<summary><b>Platform-Specific Requirements</b></summary>

- *macOS:*
    - Xcode Command Line Tools
        ```bash
        xcode-select --install
        ```
    - Dependencies needed to build `uvloop`
        ```bash
        brew install automake autoconf libtool ccache
        ```

- *Windows:*
    - Microsoft C++ Build Tools: Install the "Desktop development with C++" workload from [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022)

- *Linux:*
    ```bash
        sudo apt-get update
        sudo apt-get install -y \
            libgtk-3-dev \
            automake \
            libtool \
            libwebkit2gtk-4.1-dev \
            build-essential \
            curl \
            wget \
            file \
            libxdo-dev \
            libssl-dev \
            libayatana-appindicator3-dev \
            librsvg2-dev \
            fakeroot
    ```
</details>

> [!NOTE]
> **Windows versions prior to Windows 10 (1803)** require [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) to be installed manually.

<details>
<summary><b>Click to expand build instructions</b></summary>

### Quick Start

The project includes both a build script and Makefile for convenient building.

1. Clone the repository:
   ```bash
   git clone https://github.com/fizzi01/Perpetua.git
   cd Perpetua
   ```

2. Install Python dependencies:
   ```bash
   poetry install
   # or
   pip install .
   # or
   make install-build
   ```

3. Run the build:
   ```bash
   poetry run python build.py
   # or
   make build
   ```

### Development Setup

In development mode the two components run independently - the Rust GUI
launches via `cargo tauri dev` and the Python daemon is started manually
in a separate terminal.

1. **Install Python dependencies:**
   ```bash
   poetry install
   ```

2. **Start the daemon**:
   ```bash
   python launcher.py
   ```

3. **Install GUI dependencies (optional, if you need to modify the GUI):**
   ```bash
   cd src-gui
   npm install   # first time only
   cargo tauri dev
   ```

   The Tauri dev server supports hot-reload for the frontend. In debug
   builds the Rust binary does **not** spawn the daemon automatically.



<details>
<summary><b>Advanced Build Options</b></summary>

##### Using Poetry

```bash
# Debug build
poetry run python build.py --debug

# Skip GUI build (build daemon only)
poetry run python build.py --skip-gui

# Skip daemon build (build GUI only)
poetry run python build.py --skip-daemon

# Clean build artifacts before building
poetry run python build.py --clean
```

##### Using Make

```bash
# Debug build
make build-debug

# Build daemon only
make build-daemon

# Build GUI only
make build-gui

# Release build with clean
make build-release

# Clean build artifacts
make clean
```

</details>

<details>
<summary><b>Manual Build Steps</b></summary>

For manual builds or troubleshooting, follow these steps:

Build GUI:
```bash
cd src-gui
npm install
cargo tauri build
```

Build Daemon:
```bash
# From project root
poetry run python build.py --skip-gui
```

</details>

</details>


## Roadmap

- [X] Linux support
- [X] Multi-monitor placements
- [ ] File transfers
- [ ] Advanced clipboard format support (including proprietary formats)

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
