/* Hidden, disposable BrowserHost used by the full backend/Electron acceptance. */
const { app, BrowserWindow } = require('electron');
const { resolve } = require('node:path');
if (process.env.MAVRIS_NATIVE_APP_ACCEPTANCE !== '1' || !process.env.BUTLER_FIXTURE_PROFILE) process.exit(2);
app.setPath('userData', process.env.BUTLER_FIXTURE_PROFILE);
app.disableHardwareAcceleration();
process.env.LENGRVIS_BROWSER_HOST_ALLOW_PRIVATE_NETWORK = '1';
void app.whenReady().then(() => {
  const { BrowserHost } = require(resolve(__dirname, '../dist/main/browserHost.js'));
  const { BrowserHostWebSocketBridge } = require(resolve(__dirname, '../dist/main/browserHostBridge.js'));
  const window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  const host = new BrowserHost(() => window);
  const bridge = new BrowserHostWebSocketBridge(host, () => process.env.BUTLER_FIXTURE_BASE, () => process.env.BUTLER_FIXTURE_TOKEN);
  bridge.start();
  let stopping = false;
  const stop = async () => {
    if (stopping) return; stopping = true;
    bridge.stop(); await host.eraseLocalPrivateData(); host.destroy(); window.destroy(); app.exit(0);
  };
  process.stdin.on('data', () => { void stop(); });
  setTimeout(() => { void stop(); }, 600000);
}).catch(() => app.exit(1));
