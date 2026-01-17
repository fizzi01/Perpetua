import { combineReducers } from 'redux';

import serverReducer from './server'
import clientReducer from './client';

const reducer = combineReducers({
    server: serverReducer,
    client: clientReducer,
});

export default reducer;