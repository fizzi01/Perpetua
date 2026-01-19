import * as React from "react";         
import { platform } from '@tauri-apps/plugin-os';
import {classNames} from '../../commons/utils';

export interface ScrollAreaProps extends React.HTMLAttributes<HTMLDivElement> {
  extraPadding?: string;
  children: React.ReactNode;
}

const ScrollArea = React.forwardRef<HTMLDivElement, ScrollAreaProps>(
  ({ className, children, extraPadding, ...props }, ref) => {
    const currentPlatform = platform();
    const isWindows = currentPlatform === 'windows';
    const platformExtraPadding = isWindows ? extraPadding : '';

    return (
      <div
        ref={ref}
        className={classNames(
          "custom-scrollbar",
          platformExtraPadding,
          className
        )}
        {...props}
      >
        {children}
      </div>
    );
  }
);

ScrollArea.displayName = "ScrollArea";

export { ScrollArea };
