// Fixed CDP bridge, executed inside the account's verified container. No user
// supplied JavaScript is evaluated; input exists only in memory for this call.
const { createInterface } = await import('node:readline');
const lines = createInterface({ input: process.stdin });
let idle = setTimeout(() => process.exit(0), 90000);
for await (const line of lines) {
  clearTimeout(idle);
  await handle(JSON.parse(line));
  idle = setTimeout(() => process.exit(0), 90000);
}
clearTimeout(idle);

async function handle(input) {
const deadline = setTimeout(() => process.exit(2), 12000);
const base = 'http://127.0.0.1:9222';
let socket;
try {
  const pages = (await (await fetch(`${base}/json/list`)).json()).filter(p => p.type === 'page');
  let page = pages.find(p => p.id === input.target_id);
  const popup = page && pages.find(p => p.openerId === page.id);
  if (popup) page = popup;
  if (!page) page = pages.find(p => {
    try { return new URL(p.url).hostname === new URL(input.url).hostname; } catch { return false; }
  });
  if (!page && input.kind === 'open') {
    page = await (await fetch(`${base}/json/new?${encodeURIComponent(input.url)}`, { method: 'PUT' })).json();
  }
  if (!page) throw new Error('login_page_missing');
  // Use the local target ID, never a WebSocket address supplied by a webpage.
  socket = new WebSocket(`ws://127.0.0.1:9222/devtools/page/${page.id}`);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let sequence = 0;
  const pending = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    const item = pending.get(message.id);
    if (item) { pending.delete(message.id); message.error ? item.reject(new Error('cdp_failed')) : item.resolve(message.result); }
  };
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('cdp_timeout')); }, 2500);
    pending.set(id, { resolve: result => { clearTimeout(timer); resolve(result); }, reject: error => { clearTimeout(timer); reject(error); } });
    socket.send(JSON.stringify({ id, method, params }));
  });
  if (input.kind === 'open') await call('Page.bringToFront');
  if (input.kind === 'click') {
    await call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: input.x, y: input.y });
    await call('Input.dispatchMouseEvent', { type: 'mousePressed', x: input.x, y: input.y, button: 'left', buttons: 1, clickCount: 1 });
    await call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: input.x, y: input.y, button: 'left', buttons: 0, clickCount: 1 });
  }
  if (input.kind === 'text') await call('Input.insertText', { text: input.text });
  if (input.kind === 'key') {
    const codes = { Enter:13, Backspace:8, Tab:9, Escape:27, Delete:46, ArrowLeft:37, ArrowRight:39, ArrowUp:38, ArrowDown:40, Home:36, End:35 };
    for (const type of ['keyDown', 'keyUp']) await call('Input.dispatchKeyEvent', { type, key: input.key, windowsVirtualKeyCode: codes[input.key] });
  }
  if (input.kind === 'scroll') await call('Input.dispatchMouseEvent', { type:'mouseWheel', x: input.x, y: input.y, deltaX:0, deltaY:input.delta });
  if (input.kind === 'reload') await call('Page.reload');
  // Navigation can replace the execution context after an input/reload. Retry
  // only the read, never the action (which could submit a login twice).
  let info, frame;
  if (!['frame', 'open'].includes(input.kind)) {
    // Wait for the browser to paint the effect, without repeating the input.
    await Promise.race([
      call('Runtime.evaluate', { expression: 'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))', awaitPromise: true }).catch(() => {}),
      new Promise(resolve => setTimeout(resolve, 200)),
    ]);
  }
  for (let attempt = 0; attempt < 10; attempt++) {
    try {
      const metadata = await call('Runtime.evaluate', { expression: `JSON.stringify({
    width: innerWidth, height: innerHeight, title: document.title,
    origin: location.origin,
    suggested_name: (document.querySelector('meta[property="profile:username"]')?.content ||
      document.querySelector('[data-testid="account-name"]')?.textContent || '').trim().slice(0, 100)
  })`, returnByValue:true });
      info = JSON.parse(metadata.result.value);
      frame = await call('Page.captureScreenshot', { format:'png', captureBeyondViewport:false });
      break;
    } catch {
      if (attempt === 0 && input.kind === 'open') await call('Page.reload');
      if (attempt === 9) throw new Error('page_transition');
      await new Promise(resolve => setTimeout(resolve, 150));
    }
  }
  await new Promise(resolve => process.stdout.write(JSON.stringify({ ...info, target_id:page.id, image:frame.data }) + '\n', resolve));
} catch {
  process.stdout.write('{"error":"login_display_unavailable"}\n');
} finally { socket?.close(); clearTimeout(deadline); }
}
