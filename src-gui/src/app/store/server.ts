import { ServerStatus } from '../api/Interface'
import { ActionType } from './actions';

const initialState: ServerStatus = {
    running: false,
    uid: '',
    host: '',
    port: 0,
    heartbeat_interval: 0,
    streams_enabled: [],
    ssl_enabled: false,
    authorized_clients: [],
};

interface Action {
    type: ActionType;
    payload: Partial<ServerStatus>;
}


export default function serverReducer(state = initialState, action: Action) {                                                         
    switch (action.type) {
        case ActionType.SERVER_STATE:
            return { ...state, ...action.payload };
        default:
            return state;
    }
}