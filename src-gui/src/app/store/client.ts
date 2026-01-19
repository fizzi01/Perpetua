import { ClientStatus} from '../api/Interface'
import { ActionType } from './actions';

const initialState: ClientStatus = {
    connected: false,
    running: false,
    otp_needed: false,
    service_choice_needed: false,
    available_servers: [],
    uid: '',
    client_hostname: '',
    streams_enabled: [],
    ssl_enabled: false,
    server_info: {
        uid: '',
        host: '',
        hostname: '',
        port: 0,
        ssl: false,
        auto_reconnect: false,
    }
};

interface Action {
    type: ActionType;
    payload: Partial<ClientStatus>;
}


export default function clientReducer(state = initialState, action: Action) {                                                         
    switch (action.type) {
        case ActionType.CLIENT_STATE:
            return { ...state, ...action.payload };
        default:
            return state;
    }
}