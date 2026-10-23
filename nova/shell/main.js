// Nova's window: truly transparent (per-pixel), frameless, always on top, off the taskbar.
// Talks to Python over a local socket (127.0.0.1), one JSON message per line.
// (Not stdin/stdout: on Windows a GUI app's console pipes are unreliable.)
const { app, BrowserWindow, ipcMain, screen, protocol, net } = require('electron');
const path = require('path');
const readline = require('readline');
const sockets = require('net');
const { pathToFileURL } = require('url');

const BODY = path.join(__dirname, '..', 'body');
const opts = JSON.parse(process.env.NOVA_OPTS || '{}');
const link = sockets.connect(opts.port, '127.0.0.1');
link.setEncoding('utf8');
link.on('error', err => { console.error('link error', err); app.quit(); });
link.on('close', () => app.quit()); // Python stopped: close too
const send = msg => { if (!link.destroyed) link.write(JSON.stringify(msg) + '\n'); };

protocol.registerSchemesAsPrivileged([{ scheme: 'nova', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('ignore-gpu-blocklist');
if (!app.requestSingleInstanceLock()) app.quit();

let win = null;
let walkX = null;

function createWindow() {
  const { workArea } = screen.getPrimaryDisplay();
  const W = opts.width || 288, H = opts.height || 348;
  const right = (opts.corner || 'bottom-right').includes('right');
  const bottom = (opts.corner || 'bottom-right').includes('bottom');
  win = new BrowserWindow({
    width: W, height: H,
    x: right ? workArea.x + workArea.width - W - 8 : workArea.x + 8,
    y: bottom ? workArea.y + workArea.height - H : workArea.y + 8,
    transparent: true, backgroundColor: '#00000000', frame: false, hasShadow: false, thickFrame: false,
    resizable: false, maximizable: false, fullscreenable: false, skipTaskbar: true, alwaysOnTop: true,
    show: false, title: 'Nova',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), backgroundThrottling: false },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true);
  win.setIgnoreMouseEvents(true, { forward: true }); // clicks pass through until the mouse is over Nova
  // report problems to Python's console instead of failing silently
  win.webContents.on('console-message', (e) => { if (e.level === 'error' || e.level === 'warning') send({ event: 'log', args: [`page: ${e.message}`] }); });
  win.webContents.on('did-fail-load', (_e, code, desc, url) => send({ event: 'log', args: [`load failed ${code} ${desc} ${url}`] }));
  win.webContents.on('render-process-gone', (_e, d) => send({ event: 'log', args: [`renderer gone: ${d.reason}`] }));
  win.loadURL('nova://app/index.html');
  win.once('ready-to-show', () => win.showInactive());
  win.on('closed', () => { win = null; app.quit(); });

  // eyes follow the mouse anywhere on screen
  let last = '';
  setInterval(() => {
    if (!win || !win.isVisible()) return;
    const p = screen.getCursorScreenPoint(), b = win.getBounds();
    const nx = ((p.x - (b.x + b.width * 0.6)) / 500).toFixed(2), ny = ((p.y - (b.y + b.height * 0.45)) / 400).toFixed(2);
    if (nx + ny !== last) { last = nx + ny; win.webContents.executeJavaScript(`window.nova && nova.pointer(${nx}, ${ny})`).catch(() => {}); }
  }, 100);
}

app.whenReady().then(() => {
  protocol.handle('nova', req => {
    const file = path.normalize(path.join(BODY, decodeURIComponent(new URL(req.url).pathname)));
    if (!file.startsWith(BODY)) return new Response('no', { status: 403 });
    return net.fetch(pathToFileURL(file).toString());
  });
  createWindow();
});

ipcMain.on('api', (_e, name, args) => {
  if (!win) return;
  if (name === 'move_by') { const [x, y] = win.getPosition(); win.setPosition(Math.round(x + args[0]), Math.round(y + args[1])); walkX = null; return; }
  if (name === 'set_ignore') { win.setIgnoreMouseEvents(!!args[0], { forward: true }); return; }
  if (name === 'focus') { win.focus(); return; }
  if (name === 'set_shape') return; // not needed: this window is truly transparent
  send({ event: name, args });
});

// walking / crawling: slide the window along the bottom of the screen
ipcMain.handle('nudge', (_e, dx) => {
  if (!win) return { hit_edge: false };
  const { workArea } = screen.getDisplayMatching(win.getBounds());
  const [x, y] = win.getPosition(), w = win.getBounds().width;
  walkX = (walkX ?? x) + dx;
  const lo = workArea.x, hi = workArea.x + workArea.width - w;
  const hit = walkX < lo || walkX > hi;
  walkX = Math.max(lo, Math.min(hi, walkX));
  if (Math.round(walkX) !== x) win.setPosition(Math.round(walkX), y);
  return { hit_edge: hit };
});

readline.createInterface({ input: link }).on('line', line => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  if (!win) return;
  if (msg.js) win.webContents.executeJavaScript(msg.js).catch(err => send({ event: 'log', args: [String(err)] }));
  else if (msg.cmd === 'show') win.showInactive();
  else if (msg.cmd === 'hide') win.hide();
  else if (msg.cmd === 'quit') app.quit();
});
app.on('window-all-closed', () => app.quit());
