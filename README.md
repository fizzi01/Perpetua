![logo](https://github.com/fizzi01/PyContinuity/blob/master/logo/logo_256x256.png?raw=true)
# PyContinuity

**PyContinuity** is a cross-platform application designed to streamline control and resource sharing across multiple devices. 
It allows users to manage their devices as a unified system, offering seamless screen transitions, clipboard synchronization, and file sharing. 
PyContinuity supports **macOS** and **Windows**, bridging gaps between platforms for an efficient workflow.



## Key Features and Advantages

### 🌍 **Unified Cross-Platform Control**
- Control multiple devices using a single mouse and keyboard.
- Compatible with **macOS** and **Windows** _(Android coming soon)_.
- Reduces the need for switching peripherals, offering a more efficient workspace.

### 🖥️ **Seamless Cursor Transition**
- Emulates HDMI-like monitor transitions for the cursor.
- Configure devices spatially (e.g., left, right, up, down) for smooth, intuitive movement between systems.

### 📋 **Shared Clipboard**
- Synchronize clipboard content (text, links, images, etc.) across all connected devices.
- Enables quick copy-paste operations between systems without additional steps.

### 📁 **File Sharing**
- Transfer files between devices seamlessly:
  - Copy a file on one device and paste it directly onto another.
  - Eliminates reliance on external file-sharing tools for local network transfers.

### 🔒 **Secure Communication**
- Built-in **SSL/TLS encryption** ensures all communications between devices are secure, reliable, and private.
- No data is stored.



## Use Cases

### 🚀 **Multi-Device Workflow**
Manage multiple devices for programming, design, or content creation with a single mouse and keyboard.

### 🖱️ **Cross-Device Input Control**
For users managing both macOS and Windows systems, 
PyContinuity simplifies workflows by removing the need to physically switch peripherals or rely on additional software.

### 📂 **Streamlined Productivity**
Simplify file and clipboard sharing tasks:
- Share files without relying on cloud services or USB drives.
- Copy and paste content instantly between devices.



## How It Works

###### 1.	Setup and Configuration:

*   Install PyContinuity on each device you want to connect.
*   Configure the devices through the interface.

###### 2.	Screen Transition:

*   Configure the spatial arrangement of your devices (e.g., left, right, up, down).
*   PyContinuity enables a seamless switch between devices.

######   3.	Clipboard and File Sharing:

*   Automatically sync clipboard content across all connected devices.
*	Copy files directly from one device to another as though working in a unified environment.

## System Requirements

###### Supported Platforms
- **macOS**: Version 10.15 or later.
- **Windows**: Version 10 or later.

###### Network
- All devices must be connected to the same local network.

###### Additional Requirements
- **OpenSSL**: Required for SSL/TLS certificate generation.

## Getting Started

1. **Download PyContinuity**:
   - [Latest release](https://github.com/fizzi01/PyContinuity/releases/latest)
2. **Install**:
   - Follow the instructions in the provided [installation guide](#installation-guide).
3. **Configure**:
   - Launch the application and [configure](#configuration-details) device layout and settings.
   - Set up clipboard and file-sharing preferences.
4. **Connect**:
   - Start PyContinuity on all devices and enjoy seamless integration.

---

## Configuration Details

### **Server Configuration**
The server requires the following settings:

| Setting                                  | Description                                                                    |
|------------------------------------------|--------------------------------------------------------------------------------|
| **Clients (clients):**                   | Configure connected client positions (`up`, `down`, `left`, `right`) with IPs. |
| **Logging (logging):**                   | `True` – Enable intensive logging.                                             |
| **Screen Threshold (screen_threshold):** | `10` – Internal constant.                                                      |
| **Server IP (server_ip):**               | Leave empty, not required.                                                     |
| **Server Port (server_port):**           | `2121` – Server's listening port.                                              |
| **Use SSL (use_ssl):**                   | `True` – Use secure connections.                                               |
| **Wait (wait):**                         | `5` – Internal constant.                                                       |

#### **SSL Certificate**
The server generates an SSL certificate on the first configuration. Share this certificate with the clients for secure communication.


### **Client Configuration**
Each client must be configured to connect securely to the server. Use the following settings:

| Setting                                | Description                                                                      |
|----------------------------------------|----------------------------------------------------------------------------------|
| **Logging (logging):**                 | `True` – Enable intensive logging.                                               |
| **Server Certfile (server_certfile):** | Path to the certificate generated by the server (e.g., `/Desktop/certfile.pem`). |
| **Server IP (server_ip):**             | Not required, leave empty.                                                       |
| **Server Port (server_port):**         | `2121` – Must match the server’s port.                                           |
| **Use SSL (use_ssl):**                 | `True` – Must match the server’s setting.                                        |
| **Wait (wait):**                       | `5` – Internal constant.                                                         | 

---

## Terminal Interface Guide

The current version of PyContinuity provides a **Terminal Interface** for setup and management.

### **Menu Options**

| Option | Description                                              |
|--------|----------------------------------------------------------|
| **1**  | Manually configure the server.                           |
| **2**  | Display the current client layout (spatial arrangement). |
| **3**  | Start the server.                                        |
| **4**  | Stop the server.                                         |
| **5**  | Start the client.                                        |
| **6**  | Stop the client.                                         |
| **7**  | Edit server configuration.                               |
| **8**  | Edit client configuration.                               |
| **9**  | Reload server or client SSL certificates.                |
| **10** | Exit the application.                                    |

---

## Installation guide

Coming soon...
___

## Support

If you encounter any issues or have feature requests, please open an issue.

## License

This project is licensed under License. See the [LICENSE](https://github.com/fizzi01/pyContinuity/blob/master/LICENSE) file for details.
___

### Simplify your multi-device management with PyContinuity.
