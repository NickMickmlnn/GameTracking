const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('config', {
  apiBaseUrl: process.env.API_BASE_URL || 'http://localhost:8000'
});
