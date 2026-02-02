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