import React from "react";
import ReactDOM from "react-dom/client";
import App from "./app/App";
import { Provider } from 'react-redux';
import {store} from './app/store/store';
import "./styles/index.css";

function disableMenu() {
  if (window.location.hostname !== 'tauri.localhost') {
    return
  }

  document.addEventListener('contextmenu', e => {
    e.preventDefault();
    return false;
  }, { capture: true })

  document.addEventListener('selectstart', e => {
    e.preventDefault();
    return false;
  }, { capture: true })
}

disableMenu();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Provider store={store}>
      <App />
    </Provider>
  </React.StrictMode>,
);
