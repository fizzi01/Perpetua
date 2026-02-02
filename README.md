<div align="center">
    <img src="src-gui/src-tauri/icons/icon.png" alt="Perpetua Logo" width="128" height="128">
    <h1>Perpetua</h1>
</div>

Perpetua is an open-source, cross-platform KVM software solution inspired by Apple's Universal Control. It enables users to control multiple devices using a single keyboard and mouse, with seamless cursor transitions between devices, keyboard sharing, and clipboard synchronization across different operating systems.

Built with Python, Perpetua prioritizes performance through the integration of high-performance event loops: uvloop on macOS, and winloop on Windows. This architectural choice delivers exceptional responsiveness and low-latency input handling, ensuring smooth performance.

<picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/imgs/main_dark.png?raw=true">
    <source media="(prefers-color-scheme: light)" srcset="docs/imgs/main_light.png?raw=true">
   <img alt="Perpetua Server View" srcset="docs/imgs/main_dark.png">
</picture>

## Features

**Unified Input Control**  
Control multiple computers with a single keyboard and mouse. Move your cursor seamlessly across device boundaries as if they were multiple monitors connected to one system.

**Spatial Configuration**  
Define the physical arrangement of your devices (left, right, above, below) to enable intuitive cursor transitions that match your actual workspace layout.

**Clipboard Synchronization**  
Share clipboard content across all connected devices automatically.

**Secure by Default**  
All network communication is encrypted using TLS.


## Supported Operating Systems

Actually only Windows and MacOS are supported.

### Known Issues

*This section is reserved for documenting platform-specific issues and workarounds as they are identified.*

> [!Important]
> - **Windows**: You can't control a Windows client if there is no real mouse connected to the machine.
> - **Input Capture Conflicts**: Perpetua cannot control the mouse when other applications have exclusive input capture (e.g., video games). This is an architectural limitation.


## Usage

Launch `Perpetua` and choose whether to run as a server or client. The GUI will guide you through the necessary configuration steps.

#### Background Mode:

You can run Perpetua as a background service using the daemon mode:

```bash
# Run in daemon mode (you'll choose server or client later)
./Perpetua --daemon

# Automatically start as server
./Perpetua --daemon -s

# Automatically start as client
./Perpetua --daemon -c
```

For a full list of available commands and options:
```bash
./Perpetua --help
# or
./Perpetua -h
```



## Building from Source

<details>
<summary><b>Prerequisites</b></summary>

Perpetua requires several development tools and libraries to build successfully.

#### Python Environment:
- Python 3.11 or 3.12
- Poetry


#### GUI Framework:
- Node.js 18+ and npm
- Rust toolchain
- Tauri-cli

</details>
<details>
<summary><b>Platform-Specific Requirements</b></summary>

*macOS:*
- Xcode Command Line Tools
  ```bash
  xcode-select --install
  ```

*Windows:*
- Microsoft C++ Build Tools
  - Install the "Desktop development with C++" workload from [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022)
</details>

> [!NOTE]
> **Windows versions prior to Windows 10 (1803)** require [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) to be installed manually.


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


<details>
<summary><b>Advanced Build Options</b></summary>

#### Using Poetry

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

#### Using Make

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


## Configuration

Perpetua uses JSON to define client and server settings. Configuration file is automatically generated on first launch with sensible defaults, requiring minimal manual intervention for most use cases.

Configuration File Locations:
- macOS: `$HOME/Library/Caches/Perpetua`
- Windows: `%LOCALAPPDATA%\Perpetua`

<details>
<summary><b>Server Configuration</b></summary>

The server configuration is managed automatically for basic setup (certificate generation, network binding). However, to accept client connections, you must manually add each client to the server configuration, specifying:

- Client IP or Hostname
- Screen Position: The spatial arrangement relative to the server (left, right, top, bottom)

This configuration defines how devices are arranged in your workspace for a seamless cursor transition between them.

</details>

<details>
<summary><b>Client Configuration</b></summary>

Clients can find servers in two ways:

Auto Discovery (Default):
- Scans the local network for available servers
- Works out of the box, no configuration needed

Manual Configuration:
- Set the server's hostname or IP address directly in the config file
- Use this when auto-discovery doesn't work or you have a static network setup

</details>

<details>
<summary><b>First Connection and OTP Pairing</b></summary>

When a client connects to a new server for the first time, it needs to get the server's TLS certificate to establish a secure connection. Here's how it works:

1. The client starts the connection process
2. On the server (which must be running and listening), generate an OTP through the GUI in the Security section
3. Enter the OTP in the client when prompted (the GUI walks you through this)
4. If the certificate exchange succeeds and the client is in the server's allowlist, the connection is established
5. Done!

The OTP is just for the initial certificate exchange. After that, connections authenticate automatically using the saved certificates.

</details>

<details>
<summary><b>Configuration File Structure</b></summary>

The configuration json file is split into three sections: `server`, `client`, and `general`.

#### Server Section:

`streams_enabled` controls what the server will manage on each connected client:
- `1`: Mouse
- `4`: Keyboard  
- `12`: Clipboard

`log_level` sets the logging verbosity:
- `0`: Debug (detailed logs)
- `1`: Info (standard logs)

`authorized_clients` lists the clients that can connect. To add a new client, you only need to specify:
- `uid`: Unique identifier
- `host_name` or `ip_address`: Client's network address
- `screen_position`: Where the client is positioned relative to the server (`left`, `right`, `top`, `bottom`)

Other fields are automatically populated by the system.

#### Client Section:

The same field names have the same meaning as in the server section. The `server_info` block tells the client which server to connect to (leave it empty for auto-discovery or fill in the `host` field for manual configuration).

#### General Section:

These parameters affect the application's internal behavior. Only modify them if you know what you're doing.

#### File Structure
```json
{
    "server": {
        "uid": "...",
        "host": "0.0.0.0",
        "port": 55655,
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
                "ip_address": "192.168.1.66",
                "first_connection_date": "2026-02-02 19:09:00",
                "last_connection_date": "2026-02-02 19:16:12",
                "screen_position": "top",
                "screen_resolution": "1920x1080",
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

> [!NOTE]
> When multiple Perpetua servers are detected on the network (on auto-discovery mode),
> the GUI will present a selection dialog allowing the user to 
> choose the desired server.


## Roadmap

**Current Development Priorities:**

- **Enhanced Platform Support**
  - Linux support

- **Feature Enhancements**
  - File transfers
  - Advanced clipboard format support (including proprietary formats)
---
