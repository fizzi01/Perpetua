import { combineReducers } from 'redux';

import serverReducer from './server'

const reducer = combineReducers({
    server: serverReducer,
});

export default reducer;