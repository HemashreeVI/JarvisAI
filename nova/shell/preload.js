// Gives the page the same `window.pywebview.api` the rest of Nova's code already uses.
const { contextBridge, ipcRenderer } = require('electron');

const names = ['ready', 'clicked', 'text', 'pet', 'toggle_mute', 'next_voice', 'quiet', 'hide', 'quit',
  'move_by', 'set_shape', 'set_ignore', 'focus', 'log'];
const api = Object.fromEntries(names.map(n => [n, (...args) => { ipcRenderer.send('api', n, args); return Promise.resolve(null); }]));
api.nudge = dx => ipcRenderer.invoke('nudge', dx);

contextBridge.exposeInMainWorld('pywebview', { api, electron: true });
