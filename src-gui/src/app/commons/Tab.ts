import { ClientStatus, ServerStatus } from "../api/Interface";

export interface TabProps {
  onStatusChange: (disabled: boolean) => void;
}

export interface ServerTabProps extends TabProps {
  state: ServerStatus;
}

export interface ClientTabProps extends TabProps {
  state: ClientStatus;
}