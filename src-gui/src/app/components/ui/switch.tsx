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
        "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 shadow-sm transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      style={{
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
