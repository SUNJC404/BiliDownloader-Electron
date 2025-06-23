// preload.js
// 在 Electron 12+ 版本中，通常使用 contextBridge 来安全地暴露 API。
// 为了简化教程，我们在 main.js 中设置了 nodeIntegration: true，
// 这使得我们可以在前端JS中直接使用 require('electron')。
// 但更现代和安全的方式是使用 contextBridge。
window.ipcRenderer = require('electron').ipcRenderer;