// Nova's eye screen: drawn on a 2D canvas every frame and shown as the glowing
// image inside her glossy 3D visor. Black = visor glass, anything bright = light.

export const EYE_COLORS = {
  neutral: '#5ef0ff', happy: '#5fffd0', excited: '#8cfcff', cute: '#ff9fe3',
  love: '#ff4f9a', caring: '#8cc4ff', sad: '#6b97ff', concerned: '#ffc161',
  angry: '#ff2222', sleepy: '#4b9fbc', surprised: '#9ff4ff', thinking: '#aab5ff',
};
const BLUSH = '#ff7ea8';

function heartPath(ctx, cx, cy, size) {
  ctx.beginPath();
  for (let i = 0; i <= 40; i++) {
    const t = (Math.PI * 2 * i) / 40;
    const x = 16 * Math.sin(t) ** 3;
    const y = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t);
    const px = cx + (x * size) / 17, py = cy - (y * size) / 17;
    i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
  }
  ctx.closePath();
}

export class EyeScreen {
  constructor(width = 512, height = 346) {
    this.canvas = document.createElement('canvas');
    this.canvas.width = width;
    this.canvas.height = height;
    this.ctx = this.canvas.getContext('2d');
    this.u = width / 122; // design units -> pixels (the visor is 122 units wide)
    this.cx = width / 2;
    this.cy = height / 2;
  }

  // glowing primitives
  glow(color, blur = 9) { const c = this.ctx; c.shadowColor = color; c.shadowBlur = blur * this.u; c.fillStyle = color; c.strokeStyle = color; }
  noGlow() { this.ctx.shadowBlur = 0; }
  oval(x, y, rx, ry, color) {
    const c = this.ctx; this.glow(color);
    c.beginPath(); c.ellipse(x, y, rx, Math.max(0.5, ry), 0, 0, Math.PI * 2); c.fill();
    c.fill(); // second pass = stronger glow
  }
  highlight(x, y, r) { const c = this.ctx; this.noGlow(); c.fillStyle = '#ffffff'; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill(); }
  lid(x, y, rx, ry, innerDrop, outerDrop, side) {
    // cover the top of an eye with glass. drops: 0 = top of eye, 1 = middle
    const c = this.ctx, u = this.u; this.noGlow();
    const top = y - ry - 30 * u;
    const ix = x - side * (rx + 9 * u), ox = x + side * (rx + 9 * u);
    const iy = y - ry + innerDrop * ry, oy = y - ry + outerDrop * ry;
    c.fillStyle = '#000'; c.beginPath();
    c.moveTo(ix, top); c.lineTo(ox, top); c.lineTo(ox, oy); c.lineTo(ix, iy); c.closePath(); c.fill();
    return [ix, iy, ox, oy];
  }

  draw(s) {
    // s: {expr, color, t, open, gaze:[x,y], speaking, listening, since}
    const c = this.ctx, u = this.u, e = s.expr, col = s.color;
    c.globalCompositeOperation = 'source-over';
    c.shadowBlur = 0;
    c.fillStyle = '#000';
    c.fillRect(0, 0, this.canvas.width, this.canvas.height);
    c.lineCap = 'round'; c.lineJoin = 'round';
    const op = s.open, t = s.t;
    const gx = s.gaze[0] * u, gy = s.gaze[1] * u;
    const ey = this.cy - 1 * u + gy;

    for (const side of [-1, 1]) {
      const x = this.cx + side * 26 * u + gx;
      if (e === 'happy') {
        this.glow(col); c.lineWidth = 6.5 * u;
        c.beginPath(); c.ellipse(x, ey + 7 * u, 13 * u, 13 * u, 0, Math.PI, 0); c.stroke(); c.stroke();
      } else if (e === 'love') {
        const beat = 1 + 0.14 * Math.max(0, Math.sin(t * 7)) ** 4;
        this.glow(col); heartPath(c, x, ey, 15 * u * beat); c.fill(); c.fill();
        this.highlight(x - 5 * u, ey - 5 * u, 2.6 * u);
      } else if (e === 'cute') {
        const r = 17.5 * u;
        this.oval(x, ey, r, r * op, col);
        if (op > 0.5) {
          // glossy anime eye: darker iris ring, big + small sparkles that twinkle
          this.noGlow(); c.fillStyle = 'rgba(120,20,90,0.45)';
          c.beginPath(); c.ellipse(x, ey + 3 * u, r * 0.62, r * 0.62 * op, 0, 0, Math.PI * 2); c.fill();
          const tw = 1 + 0.18 * Math.sin(t * 5 + side);
          this.highlight(x - 6 * u, ey - 6.5 * u, 6.2 * u * tw);
          this.highlight(x + 6.5 * u, ey + 6 * u, 3 * u * tw);
          this.highlight(x + 9 * u, ey - 9.5 * u, 1.7 * u);
        }
      } else if (e === 'excited') {
        this.oval(x, ey, 15 * u, 19 * u * op, col);
        if (op > 0.5) { this.highlight(x - 5 * u, ey - 8 * u, 4.6 * u); this.highlight(x + 5 * u, ey + 6 * u, 2 * u); }
      } else if (e === 'surprised') {
        const pop = 1 + Math.max(0, 1 - s.since / 0.3) * 0.25, r = 16.5 * u * pop;
        this.oval(x, ey, r, r * op, col);
        this.noGlow(); c.fillStyle = '#000';
        c.beginPath(); c.ellipse(x, ey, r * 0.5, r * 0.5 * op, 0, 0, Math.PI * 2); c.fill();
      } else if (e === 'angry') {
        const rx = 14 * u, ry = 15 * u * op;
        this.oval(x, ey, rx, ry, col);
        const [ix, iy, ox, oy] = this.lid(x, ey, rx, ry, 0.95, 0.0, side);
        this.glow(col); c.lineWidth = 4.5 * u;
        c.beginPath(); c.moveTo(ix, iy - 4 * u); c.lineTo(ox, oy - 4 * u); c.stroke(); // furious brow
      } else if (e === 'caring' || e === 'sad') {
        const rx = 12 * u, ry = (e === 'caring' ? 15 : 14) * u * op;
        this.oval(x, ey, rx, ry, col);
        this.lid(x, ey, rx, ry, 0.05, e === 'caring' ? 0.6 : 0.9, side);
        if (op > 0.5) this.highlight(x - 4 * u, ey - 2 * u, 2.6 * u);
        if (e === 'sad' && side === 1) { // a tear welling up
          const k = (t * 0.6) % 1;
          this.glow('#8fd6ff', 4);
          c.beginPath(); c.ellipse(x + 9 * u, ey + 12 * u + k * 40 * u, 2.6 * u, 3.6 * u, 0, 0, Math.PI * 2); c.fill();
        }
      } else if (e === 'concerned') {
        const rx = 12 * u, ry = 15 * u * op;
        this.oval(x, ey, rx, ry, col);
        this.lid(x, ey, rx, ry, side < 0 ? 0.5 : 0.25, side < 0 ? 0.25 : 0.5, side);
      } else if (e === 'sleepy') {
        const droop = 0.4 + 0.35 * (0.5 + 0.5 * Math.sin(t * 0.9));
        const rx = 13 * u, ry = 14 * u;
        this.oval(x, ey + 4 * u, rx, ry, col);
        this.lid(x, ey + 4 * u, rx, ry, droop * 2, droop * 2, side);
      } else if (e === 'thinking') {
        const rx = 11 * u, ry = 14 * u * op;
        this.oval(x, ey, rx, ry, col);
        if (side > 0) this.lid(x, ey, rx, ry, 0.45, 0.2, side);
      } else {
        const ry = (s.listening ? 17.5 : 16) * u * op;
        this.oval(x, ey, 12.5 * u, ry, col);
        if (op > 0.5) this.highlight(x - 4 * u, ey - 7 * u, 3.2 * u);
      }
    }

    if (['happy', 'cute', 'love', 'caring', 'excited'].includes(e)) {
      this.glow(BLUSH, 6);
      c.globalAlpha = e === 'caring' ? 0.45 : 0.8;
      for (const side of [-1, 1]) {
        c.beginPath(); c.ellipse(this.cx + side * 41 * u, this.cy + 22 * u, 9.5 * u, 3.8 * u, 0, 0, Math.PI * 2); c.fill();
      }
      c.globalAlpha = 1;
    }
    this.mouth(this.cx, this.cy + 25 * u, s);
  }

  mouth(x, y, s) {
    const c = this.ctx, u = this.u, e = s.expr;
    this.glow(s.color, 5);
    c.lineWidth = 3 * u;
    if (s.speaking) { // equalizer while talking
      [-8, 0, 8].forEach((off, i) => {
        const h = (3 + 5 * Math.abs(Math.sin(s.t * (11 + i * 3) + i))) * u;
        c.lineWidth = 4.2 * u;
        c.beginPath(); c.moveTo(x + off * u, y - h); c.lineTo(x + off * u, y + h); c.stroke();
      });
      return;
    }
    c.beginPath();
    if (['happy', 'excited', 'love'].includes(e)) c.ellipse(x, y - 3 * u, 8 * u, 6 * u, 0, 0.2, Math.PI - 0.2);
    else if (e === 'cute') { // cat mouth: ω
      c.ellipse(x - 4.5 * u, y - 2 * u, 4.5 * u, 4 * u, 0, 0, Math.PI); c.moveTo(x + 9 * u, y - 2 * u);
      c.ellipse(x + 4.5 * u, y - 2 * u, 4.5 * u, 4 * u, 0, 0, Math.PI);
    } else if (e === 'sad' || e === 'angry') c.ellipse(x, y + 5 * u, 8 * u, 6 * u, 0, Math.PI + 0.3, -0.3);
    else if (e === 'concerned') { c.moveTo(x - 9 * u, y + 2 * u); c.bezierCurveTo(x - 4 * u, y - 4 * u, x + 1 * u, y + 6 * u, x + 9 * u, y); }
    else if (e === 'surprised') c.ellipse(x, y + 1 * u, 4 * u, 4.6 * u, 0, 0, Math.PI * 2);
    else if (e === 'caring') c.ellipse(x, y - 2 * u, 6 * u, 4 * u, 0, 0.25, Math.PI - 0.25);
    else return;
    c.stroke();
  }
}

// Pip's tiny face screen
export class PetEyes {
  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = 256; this.canvas.height = 150;
    this.ctx = this.canvas.getContext('2d');
  }
  draw(mood, t, color, look = 0) {
    const c = this.ctx, W = 256, H = 150;
    c.shadowBlur = 0; c.fillStyle = '#000'; c.fillRect(0, 0, W, H);
    c.shadowColor = color; c.shadowBlur = 18; c.fillStyle = color; c.strokeStyle = color; c.lineCap = 'round'; c.lineWidth = 11;
    for (const side of [-1, 1]) {
      const x = W / 2 + side * 52 + look * 14, y = H / 2 - 4;
      c.beginPath();
      if (['happy', 'petted', 'love'].includes(mood)) { c.ellipse(x, y + 10, 20, 20, 0, Math.PI, 0); c.stroke(); }
      else if (mood === 'scared') { const d = -side; c.moveTo(x - d * 17, y - 17); c.lineTo(x + d * 15, y); c.lineTo(x - d * 17, y + 17); c.stroke(); }
      else if (mood === 'sleepy') { c.ellipse(x, y - 4, 20, 14, 0, 0.1, Math.PI - 0.1); c.stroke(); }
      else {
        const blink = (t % 4.3) < 0.12 ? 0.12 : 1;
        c.ellipse(x, y, 15, (mood === 'sad' ? 15 : 21) * blink, 0, 0, Math.PI * 2); c.fill();
        if (blink > 0.5) { c.shadowBlur = 0; c.fillStyle = '#fff'; c.beginPath(); c.arc(x - 5, y - 8, 5, 0, Math.PI * 2); c.fill(); c.fillStyle = color; c.shadowBlur = 18; }
      }
    }
    if (['love', 'petted', 'happy'].includes(mood)) {
      c.shadowColor = BLUSH; c.fillStyle = BLUSH; c.globalAlpha = 0.75;
      for (const side of [-1, 1]) { c.beginPath(); c.ellipse(W / 2 + side * 92, H / 2 + 34, 20, 9, 0, 0, Math.PI * 2); c.fill(); }
      c.globalAlpha = 1;
    }
  }
}
