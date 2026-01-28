/*
 * Perpatua - open-source and cross-platform KVM software.
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

import { useEffect, useState } from "react"
import { cn } from "./libs/utils"
import { TauriAppWindowProvider } from "./contexts/plugin-window"
import { Gnome, MacOS, Windows } from "./controls"
import { getOsType } from "./libs/plugin-os"
import type { WindowControlsProps } from "./types"

export function WindowControls({
  platform,
  justify = false,
  hide = false,
  hideMethod = "display",
  // linuxDesktop = "gnome",
  className,
  ...props
}: WindowControlsProps) {
  const [osType, setOsType] = useState<string | undefined>(undefined)

  useEffect(() => {
    getOsType().then((type) => {
      setOsType(type)
    })
  }, [])

  const customClass = cn(
    "flex",
    className,
    hide && (hideMethod === "display" ? "hidden" : "invisible")
  )

  // Determine the default platform based on the operating system if not specified
  if (!platform) {
    switch (osType) {
      case "macos":
        platform = "macos"
        break
      case "linux":
        platform = "gnome"
        break
      default:
        platform = "windows"
    }
  }

  const ControlsComponent = () => {
    switch (platform) {
      case "windows":
        return (
          <Windows
            className={cn(customClass, justify && "ml-auto")}
            {...props}
          />
        )
      case "macos":
        return (
          <MacOS className={cn(customClass, justify && "ml-0")} {...props} />
        )
      case "gnome":
        return (
          <Gnome className={cn(customClass, justify && "ml-auto")} {...props} />
        )
      default:
        return (
          <Windows
            className={cn(customClass, justify && "ml-auto")}
            {...props}
          />
        )
    }
  }

  return (
    <TauriAppWindowProvider>
      <ControlsComponent />
    </TauriAppWindowProvider>
  )
}
