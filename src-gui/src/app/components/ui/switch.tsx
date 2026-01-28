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

import * as React from "react"
import * as SwitchPrimitives from "@radix-ui/react-switch"

import { cn } from "../../commons/utils"

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitives.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitives.Root>
>(({ className, style, ...props }, ref) => {
  const [isChecked, setIsChecked] = React.useState(props.checked || props.defaultChecked || false);

  React.useEffect(() => {
    if (props.checked !== undefined) {
      setIsChecked(props.checked);
    }
  }, [props.checked]);

  return (
    <SwitchPrimitives.Root
      className={cn(
        "peer inline-flex h-5 w-9 shrink-0 items-center rounded-full border-2 shadow-sm transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50",
        className
      )}
      style={{
        cursor: props.disabled ? '' : 'pointer',
        backgroundColor: isChecked ? 'var(--app-primary)' : 'var(--app-input-bg)',
        borderColor: isChecked ? 'var(--app-primary)' : 'var(--app-input-border)',
        outlineColor: 'var(--app-primary)',
        '--tw-ring-color': 'var(--app-primary)',
        ...style
      } as React.CSSProperties}
      {...props}
      ref={ref}
      onCheckedChange={(checked) => {
        setIsChecked(checked);
        props.onCheckedChange?.(checked);
      }}
    >
      <SwitchPrimitives.Thumb
        className={cn(
          "pointer-events-none block h-4 w-4 rounded-full shadow-lg ring-0 transition-all duration-200 data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0"
        )}
        style={{
          backgroundColor: 'white'
        }}
      />
    </SwitchPrimitives.Root>
  );
})
Switch.displayName = SwitchPrimitives.Root.displayName

export { Switch }
