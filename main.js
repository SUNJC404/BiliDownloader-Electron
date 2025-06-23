// main.js

// MODIFICATION 1: Import the 'shell' module
const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');

let mainWindow;
let pythonSubprocess;

// --- 1. 启动 Python 后端服务 ---
// function startPythonServer() {
//   const scriptPath = path.join(__dirname, 'backend', 'app.py');
  
//   pythonSubprocess = spawn('python', [scriptPath]);

//   pythonSubprocess.stdout.on('data', (data) => {
//     console.log(`Python stdout: ${data}`);
//   });

//   pythonSubprocess.stderr.on('data', (data) => {
//     console.error(`Python stderr: ${data}`);
//   });

//   pythonSubprocess.on('close', (code) => {
//     console.log(`Python subprocess exited with code ${code}`);
//   });
// }

function startPythonServer() {
  const scriptPath = path.join(__dirname, 'backend', 'app.py');

  const options = {
    env: {
      ...process.env, // Inherit parent's environment variables
      PYTHONUTF8: '1' // Force Python's stdout/stderr to use UTF-8
    }
  };
  
  pythonSubprocess = spawn('python', [scriptPath], options); // Pass options to spawn
  
  // The rest of the function remains the same
  pythonSubprocess.stdout.on('data', (data) => {
    console.log(`Python stdout: ${data}`);
  });

  pythonSubprocess.stderr.on('data', (data) => {
    console.error(`Python stderr: ${data}`);
  });

  pythonSubprocess.on('close', (code) => {
    console.log(`Python subprocess exited with code ${code}`);
  });
}

// --- 2. 创建主应用窗口 ---
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: false, 
      nodeIntegration: true,
    }
  });

  setTimeout(() => {
    mainWindow.loadURL('http://127.0.0.1:5000');
  }, 3000);
  
  // mainWindow.webContents.openDevTools();

  // ===================================================================
  // MODIFICATION 2: Add handlers to open external links in default browser
  // ===================================================================
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    // This handler catches links that would open a new window (e.g., target="_blank")
    console.log(`Intercepted new window request for: ${url}`);
    // Use the shell module to open the URL in the user's default browser
    shell.openExternal(url);
    // Deny Electron from creating a new window for this URL
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    // This handler catches clicks on links that would navigate the current window
    // We only want to navigate within our app (to other pages like /reviewer)
    // If the URL is not our local server, open it externally.
    if (!url.startsWith('http://127.0.0.1:5000')) {
        console.log(`Intercepted navigation to: ${url}`);
        event.preventDefault(); // Prevent the current window from navigating
        shell.openExternal(url); // Open in the default browser instead
    }
  });
  // ===================================================================
}

// --- 3. 实现“浏览文件夹”的后台逻辑 ---
ipcMain.handle('dialog:openDirectory', async () => {
  const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory']
  });
  if (!canceled) {
    return filePaths[0];
  }
});


// --- 4. Electron 应用生命周期事件 ---
app.whenReady().then(() => {
  startPythonServer();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('will-quit', () => {
  if (pythonSubprocess) {
    if (process.platform === 'win32') {
      exec(`taskkill /PID ${pythonSubprocess.pid} /F /T`);
    } else {
      pythonSubprocess.kill();
    }
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});