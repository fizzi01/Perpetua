import platform
import sys
import time
from packaging import version

kIOHIDAccessTypeDenied = 1
kIOHIDAccessTypeGranted = 0
kIOHIDAccessTypeUnknown = 2
kIOHIDRequestTypeListenEvent = 1
kIOHIDRequestTypePostEvent = 0


def is_keyboard_verified(ioset):
    status = ioset['IOHIDCheckAccess'](kIOHIDRequestTypeListenEvent)
    print("[CHECK] Keyboard verification status: {}".format(status))

    return status == kIOHIDAccessTypeGranted


def is_accessibility_verified():
    import HIServices
    return HIServices.AXIsProcessTrusted()


def is_should_check_input():
    return version.parse(platform.mac_ver()[0]) >= version.parse("10.15")


def run_input_checks(ioset):
    try:

        time.sleep(1)
        is_verified = is_keyboard_verified(ioset)
        print("[CHECK] Is keyboard verified: {}".format(is_verified))

    except Exception as ex:
        print(ex)


def run_accessibility_checks():
    try:

        time.sleep(1)
        is_trusted = is_accessibility_verified()
        print("[CHECK] Accessibilty verified: {}".format(is_trusted))
        return is_trusted

    except Exception as ex:
        print(ex)
        return False


def load_iot():
    try:
        import objc
        from Foundation import NSBundle
        IOKit = NSBundle.bundleWithIdentifier_('com.apple.framework.IOKit')

        ioset = {}
        functions = [
            ("IOHIDRequestAccess", b"BI"),
            ("IOHIDCheckAccess", b"II"),
        ]

        objc.loadBundleFunctions(IOKit, ioset, functions)

        return ioset

    except Exception as e:
        print(e)


def on_accessibility_click():
    try:
        import HIServices
        from ApplicationServices import kAXTrustedCheckOptionPrompt

        HIServices.AXIsProcessTrustedWithOptions({
            kAXTrustedCheckOptionPrompt: True
        })

        print("[CHECK] Called accessibility")
        return run_accessibility_checks()

    except Exception as ex:
        print(ex)
        return False


def manual_request_input_access():
    try:
        from AppKit import NSWorkspace, NSURL

        url = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
        url = NSURL.alloc().initWithString_(url)
        NSWorkspace.sharedWorkspace().openURL_(url)

    except Exception as e:
        print(e)


def open_accessibility_settings():
    try:
        from AppKit import NSWorkspace, NSURL

        url = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        url = NSURL.alloc().initWithString_(url)
        NSWorkspace.sharedWorkspace().openURL_(url)

    except Exception as e:
        print(e)


def on_input_monitor_click(
        ioset,
):
    try:
        result = ioset['IOHIDRequestAccess'](kIOHIDRequestTypeListenEvent)
        print("[CHECK] Called IOHIDRequestAccess, result={}".format(result))

        manual_request_input_access()

        run_input_checks(ioset)

    except Exception as e:
        print(e)


def is_mac_os():
    return sys.platform == "darwin"


def check_osx_permissions():
    try:
        print("[CHECK] Loading IOT")
        ioset = load_iot()
        is_input_monitor_required = is_should_check_input()

        if is_input_monitor_required:
            print("[CHECK] Input monitoring is required")

            is_keyboard_ok = is_keyboard_verified(ioset)
            print("[CHECK] Is keyboard trusted: {}".format(is_keyboard_ok))

            if not is_keyboard_ok:
                on_input_monitor_click(ioset)
                """thread = Thread(target=on_input_monitor_click, args=(
                    ioset,
                ))
                thread.start()"""

            else:
                print("[CHECK] Is keyboard trusted: {}".format(is_keyboard_ok))

                is_accessibility_ok = is_accessibility_verified()
                print("[CHECK] Is accessibility trusted: {}".format(is_accessibility_ok))

                if not is_accessibility_ok:
                    is_accessibility_ok = on_accessibility_click()

                # if not is_accessibility_ok:
                #     open_accessibility_settings()

                return is_accessibility_ok

    except Exception as e:
        print(e)
