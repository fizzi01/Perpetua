<div align="center">
    <img src="src-gui/src-tauri/icons/icon.png" alt="Perpetua Logo" width="128" height="128">
    <h1>Perpetua</h1>
</div>

Perpetua is an open-source, cross-platform KVM software solution inspired by Apple's Universal Control. It enables users to control multiple devices using a single keyboard and mouse, with seamless cursor transitions between devices, keyboard sharing, and clipboard synchronization across different operating systems.

Unlike hardware KVM switches, Perpetua operates entirely over the local network, eliminating the need for physical peripherals or cables while maintaining secure, encrypted communication between devices.

Built with Python, Perpetua prioritizes performance through the integration of high-performance event loops: uvloop on macOS, and winloop on Windows. This architectural choice delivers exceptional responsiveness and low-latency input handling, ensuring smooth performance.

<picture>
   <source srcset="docs/imgs/main.png">
   <img alt="Perpetua Server View" srcset="docs/imgs/main.png">
</picture>

## Features

**Unified Input Control**  
Control multiple computers with a single keyboard and mouse. Move your cursor seamlessly across device boundaries as if they were multiple monitors connected to one system.

**Spatial Configuration**  
Define the physical arrangement of your devices (left, right, above, below) to enable intuitive cursor transitions that match your actual workspace layout.

**Clipboard Synchronization**  
Share clipboard content including text, images, and rich formats across all connected devices automatically.

**Secure by Default**  
All network communication is encrypted using TLS.

---


## Supported Operating Systems

Actually only Windows and MacOS are supported.

### Known Issues

*This section is reserved for documenting platform-specific issues and workarounds as they are identified.*

> [!Important]
> - **Windows**: You can't control a Windows client if there is no real mouse connected to the machine.
> - **Input Capture Conflicts**: Perpetua cannot control the mouse when other applications have exclusive input capture (e.g., video games). This is an architectural limitation.

---

## Building from Source

### Prerequisites

Perpetua requires several development tools and libraries to build successfully.

**Python Environment:**
- Python 3.11 or 3.12
- Poetry


**GUI Framework:**
- Node.js 18+ and npm
- Rust toolchain
- Tauri-cli

**Platform-Specific Requirements:**

*macOS:*
- Xcode Command Line Tools
  ```bash
  xcode-select --install
  ```

*Windows:*
- Microsoft C++ Build Tools
  - Install the "Desktop development with C++" workload from [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022)

> [!NOTE]
> **Windows versions prior to Windows 10 (1803)** require [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) to be installed manually.

---

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

---

## Configuration

Perpetua uses JSON configuration files to define client and server settings. Configuration files are **automatically generated on first launch** with sensible defaults, requiring minimal manual intervention for most use cases.

Configuration File Locations:
- macOS: `$HOME/Library/Caches/Perpetua`
- Windows: `%LOCALAPPDATA%\Perpetua`

<details>
<summary><b>Server Configuration</b></summary>

The server configuration is managed automatically and typically does not require manual modification. Perpetua handles server setup, certificate generation, and network binding autonomously.

Default behavior:
- Generates self-signed TLS certificates
- Manages client authentication and pairing

</details>

<details>
<summary><b>Client Configuration</b></summary>

The client configuration allows two discovery modes:

**Automatic Discovery (Default):**
- Perpetua automatically discovers available servers on the local network
- No manual configuration required
- Ideal for single-server environments

**Manual Server Specification:**
- Explicitly define the server hostname or IP address in the configuration file
- Useful for static network setups or when automatic discovery fails

</details>

<details>
<summary><b>First Connection and OTP Pairing</b></summary>

On the first connection between a client and server, Perpetua implements a secure pairing process:

1. **OTP Generation**: The server generates a one-time password (OTP)
2. **OTP Entry**: The user must enter this OTP on the client side
3. **Certificate Exchange**: Upon successful OTP verification, the server shares its TLS certificate with the client
4. **Trusted Connection**: Subsequent connections are authenticated automatically using the exchanged certificates

This mechanism ensures secure, autonomous certificate sharing without manual management.

</details>

<details>
<summary><b>Configuration Files Structure</b></summary>

**Client Configuration (`client_config.json`):**
```json
{
  "server_info": {
    "uid": "",
    "host": "",
    "hostname": "",
    "port": 55655,
    "heartbeat_interval": 1,
    "auto_reconnect": true,
    "ssl": true,
    "additional_params": {}
  },
  "uid": "client-unique-identifier",
  "client_hostname": "your-hostname",
  "streams_enabled": {
    "0": true,
    "1": true,
    "4": true,
    "12": true
  },
  "ssl_enabled": true,
  "log_level": 0,
  "log_to_file": false,
  "log_file_path": null
}
```

> For automatic server discovery, leave the `server_info` 
> connection fields empty (`host`, `hostname`, `uid`).
> Perpetua will automatically detect available servers on the local network.

For manual server configuration, populate the `host` field with the server's IP address or hostname.

</details>

> [!NOTE]
> When multiple Perpetua servers are detected on the network,
> the GUI presents a selection dialog allowing the user to 
> choose the desired server.

---

## Roadmap

**Current Development Priorities:**

- **Enhanced Platform Support**
  - Linux support

- **Feature Enhancements**
  - File transfers
  - Advanced clipboard format support (including proprietary formats)
---
