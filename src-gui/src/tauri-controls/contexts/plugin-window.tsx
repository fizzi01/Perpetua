/*
 * Perpetua - open-source and cross-platform KVM software.
 * Copyright (c) 2026 Federico Izzi.
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 */

import { getCurrentWindow, Window } from "@tauri-apps/api/window"
import React, { createContext, useCallback, useEffect, useState } from "react"
import { getOsType } from "../libs/plugin-os"

interface TauriAppWindowContextType {
  appWindow: Window | null
  isWindowMaximized: boolean
  minimizeWindow: () => Promise<void>
  maximizeWindow: () => Promise<void>
  fullscreenWindow: () => Promise<void>
  closeWindow: () => Promise<void>
  isResizable: boolean
}

const TauriAppWindowContext = createContext<TauriAppWindowContextType>({
  appWindow: null,
  isWindowMaximized: false,
  minimizeWindow: () => Promise.resolve(),
  maximizeWindow: () => Promise.resolve(),
  fullscreenWindow: () => Promise.resolve(),
  closeWindow: () => Promise.resolve(),
  isResizable: false,
})

interface TauriAppWindowProviderProps {
  children: React.ReactNode
}

export const TauriAppWindowProvider: React.FC<TauriAppWindowProviderProps> = ({
  children,
}: any) => {
  const [appWindow, setAppWindow] = useState<Window | null>(null)
  const [isWindowMaximized, setIsWindowMaximized] = useState(false)
  const [isResizable, setIsResizable] = useState(false)

  // Fetch the Tauri window plugin when the component mounts
  // Dynamically import plugin-window for next.js, sveltekit, nuxt etc. support:
  // https://github.com/tauri-apps/plugins-workspace/issues/217
  useEffect(() => {
    if (typeof window !== "undefined") {
      import("@tauri-apps/api").then((module) => {
        module.window.getAllWindows().then((wins) => setAppWindow(wins.filter((w) => w.label === "main")[0]))
      })
    }
  }, [])

  // Update the isWindowMaximized state when the window is resized
  const updateIsWindowMaximized = useCallback(async () => {
    if (appWindow) {
      const _isWindowMaximized = await appWindow.isMaximized()
      setIsWindowMaximized(_isWindowMaximized)
    }
  }, [appWindow])

  useEffect(() => {
    getOsType().then((osname) => {
      // temporary: https://github.com/agmmnn/tauri-controls/issues/10#issuecomment-1675884962
      if (osname !== "macos") {
        updateIsResizable()
        updateIsWindowMaximized()
        let unlisten: () => void = () => {}

        const listen = async () => {
          if (appWindow) {
            unlisten = await appWindow.onResized(() => {
              updateIsWindowMaximized()
            })
          }
        }
        listen()

        // Cleanup the listener when the component unmounts
        return () => unlisten && unlisten()
      }
    })
  }, [appWindow, updateIsWindowMaximized])

  const minimizeWindow = async () => {
    if (appWindow) {
      await appWindow.minimize()
    }
  }

  const maximizeWindow = async () => {
    if (appWindow) {
      await appWindow.toggleMaximize()
    }
  }

  const fullscreenWindow = async () => {
    if (appWindow) {
      const fullscreen = await appWindow.isFullscreen()
      if (fullscreen) {
        await appWindow.setFullscreen(false)
      } else {
        await appWindow.setFullscreen(true)
      }
    }
  }

  const closeWindow = async () => {
    if (appWindow) {
      await appWindow.close()
    }
  }

  const updateIsResizable = async () => {
    let resizable = await getCurrentWindow().isResizable()
    setIsResizable(resizable)
  }

  // Provide the context values to the children components
  return (
    <TauriAppWindowContext.Provider
      value={{
        appWindow,
        isWindowMaximized,
        minimizeWindow,
        maximizeWindow,
        fullscreenWindow,
        closeWindow,
        isResizable,
      }}
    >
      {children}
    </TauriAppWindowContext.Provider>
  )
}

export default TauriAppWindowContext
