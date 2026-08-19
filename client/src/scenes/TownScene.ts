// 主小镇场景（Phaser v4）
// 设计说明（为什么这样设计）：
// - 地图从共享 town_map.json 读取（与服务端寻路同源）：ai-town 格式双层
//   bgtiles（地面）+ 双层 objmap（物件/障碍），逐层渲染；碰撞只挂 obj 层
//   （obj 层非空 = 阻挡，与 ai-town 的 blockedWithPositions 语义一致）。
// - 角色用 folk 精灵表（每角色 96×128 块：down/left/right/up 各 3 帧行走），
//   玩家与居民共用同一套帧计算（mapData.walkFrames）。
// - 相机 zoom 可切换（键 1/2/3）：近身逛街 ×2 与"上帝视角"×1 之间的观感
//   差异很大，留给玩家自己选；为让文字清晰，文字一律"大字号渲染 + scale
//   缩小"——小纹理被 zoom 放大会糊，大纹理缩放后才锐利。
// - 居民移动是插值的：服务端每 1/3 秒广播一个单格目标点，客户端每帧
//   "按时到位"逼近（速度 = 剩余距离 × 广播频率）——平滑连续、不切墙角、
//   转向精确（2026-08-19 视觉优化，修掉逐秒 96px 跳变）。
// - 头顶名牌 = 名字 + 动作 emoji（actionEmoji 关键词映射，斯坦福
//   pronunciato 的低成本版），让"正在干嘛"扫一眼可见。
// - 深度即 y：所有角色（玩家/居民/名牌）depth = y——脚下越靠下越靠前，
//   聚集点（餐馆、广场）居民互相遮挡才正确。
// - 暖色滤镜（B 键开关）：mage3 石城调性偏暗，用相机级 ColorMatrix
//   提亮 + 加饱和 + 微暖色偏移拉近"星露谷式治愈"明度带，不动素材。
// - 文字 UI（对话/事件日志/输入）走 DOM HUD，画布只画像素世界（头顶名牌）。
// - 对话打开时禁用移动，避免边打字边走路。

import Phaser from "phaser";
import { NetClient, type ServerMessage } from "../net/client";
import { Hud } from "../ui/hud";
import { actionEmoji } from "../world/actionEmoji";
import { CHARACTER_INDEX, FOLK_FRAME, PLAYER_CHARACTER, walkFrames, type Direction } from "../world/mapData";

interface TownMapData {
  cols: number;
  rows: number;
  tileDim: number;
  tileset: string;
  tilesetCols: number;
  /** 行主序图层，-1 = 空 */
  bgLayers: number[][][];
  objLayers: number[][][];
  walkable: boolean[][];
  playerSpawn: { col: number; row: number };
}

/** 相机缩放档位（键 1/2/3 直达）。×1 一屏约 30×20 格，接近斯坦福"上帝视角" */
const CAMERA_ZOOMS = [2, 1.5, 1] as const;
const ZOOM_KEYS = ["ONE", "TWO", "THREE"] as const;
const DEFAULT_ZOOM_INDEX = 0;
const MOVE_REPORT_INTERVAL_MS = 100;
const CHAT_RANGE_PX = FOLK_FRAME * 3 + 8; // 与服务端 CHAT_RANGE_TILES 对齐
const PLAYER_SPEED = 110; // 像素/秒

// —— 居民插值参数 ——
// 服务端 3 拍/秒广播单格目标点（见 engine.BROADCAST_INTERVAL_SECONDS）。
// "按时到位"：速度 = 剩余距离 × 每秒拍数——正好在下一拍到达，既不会提前
// 到位后停顿（每格一顿的脉冲感），也永不清零剩余距离后的僵直。
const SERVER_TICKS_PER_SECOND = 3;
const RESIDENT_MAX_SPEED = 240; // 上限：重连/大偏移时也别让居民"冲刺"
const RESIDENT_SNAP_DISTANCE = FOLK_FRAME * 4; // 超 4 格视为瞬移（重连），直接贴齐
const RESIDENT_ARRIVE_EPSILON = 0.5; // 剩余距离小于此值视为已到达

const LABEL_OFFSET_Y = 13;
const HUD_DEPTH = 99999; // 状态栏永远在最上层（角色 depth = y 最大约 1600）

/** 一个居民的客户端视觉状态：精灵 + 名牌 + 插值目标（数据收口，避免散落 getData） */
interface ResidentVisual {
  id: string;
  name: string;
  sprite: Phaser.GameObjects.Sprite;
  label: Phaser.GameObjects.Text;
  /** 服务端最新目标点（世界坐标），update() 里逐帧逼近 */
  target: { x: number; y: number };
  /** 最近一次服务端动作文本（走近提示用） */
  action: string;
  /** 当前头顶 emoji（变化才 setText，避免每拍重建文字纹理） */
  emoji: string;
  moving: boolean;
}

export class TownScene extends Phaser.Scene {
  private player!: Phaser.Physics.Arcade.Sprite;
  private cursors!: Phaser.Types.Input.Keyboard.CursorKeys;
  private net!: NetClient;
  private statusText!: Phaser.GameObjects.Text;
  private hud!: Hud;
  private lastReportAt = 0;
  private lastFrameTime = 0;
  private residents = new Map<string, ResidentVisual>();
  private chattingIds = new Set<string>();
  private chatTarget: string | null = null;
  private tileDim = FOLK_FRAME;
  private worldWidth = 0;
  private worldHeight = 0;
  private zoomIndex = DEFAULT_ZOOM_INDEX;
  private warmFilter: Phaser.Filters.ColorMatrix | null = null;
  private warmOn = true;
  private gameTimeLabel = "";

  constructor() {
    super("TownScene");
  }

  preload(): void {
    this.load.image("town-tiles", "assets/tiles/magecity.png");
    this.load.spritesheet("folk", "assets/sprites/32x32folk.png", {
      frameWidth: FOLK_FRAME,
      frameHeight: FOLK_FRAME,
    });
    this.load.json("town-map", "assets/town_map.json");
  }

  create(): void {
    // —— 瓦片地图（从共享 JSON 读，与服务端寻路同源）——
    // ai-town 格式是 4 张图层（2 bg + 2 obj）。Phaser 的 make.tilemap({data})
    // 一次只建一层，所以每层建一个 tilemap 对象、各贴一张 layer——
    // 渲染顺序 = 创建顺序（bg0 → bg1 → obj0 → obj1），obj 层自然盖在地面之上。
    // 图层 depth 恒为 0，角色 depth = y（≥16），角色永远在图层之上——与旧版
    // "创建顺序在上"的视觉语义一致。
    const townMap = this.cache.json.get("town-map") as TownMapData;
    this.tileDim = townMap.tileDim;
    this.worldWidth = townMap.cols * this.tileDim;
    this.worldHeight = townMap.rows * this.tileDim;

    const objLayers: Phaser.Tilemaps.TilemapLayer[] = [];
    for (const layerData of [...townMap.bgLayers, ...townMap.objLayers]) {
      const isObj = !townMap.bgLayers.includes(layerData);
      const map = this.make.tilemap({
        data: layerData,
        tileWidth: this.tileDim,
        tileHeight: this.tileDim,
      });
      const tiles = map.addTilesetImage("town-tiles");
      if (!tiles) throw new Error("tileset 加载失败");
      const layer = map.createLayer(0, tiles, 0, 0);
      if (!layer) throw new Error("layer 创建失败");
      if (isObj && layer instanceof Phaser.Tilemaps.TilemapLayer) {
        // 碰撞：obj 层非空即阻挡（-1 = 空），与 ai-town 的
        // blockedWithPositions 语义一致；bg 层不参与碰撞
        layer.setCollisionByExclusion([-1]);
        objLayers.push(layer);
      }
    }

    // —— 玩家 ——
    const pc = townMap.playerSpawn.col * this.tileDim + this.tileDim / 2;
    const pr = townMap.playerSpawn.row * this.tileDim + this.tileDim / 2;
    this.player = this.physics.add.sprite(pc, pr, "folk", walkFrames(PLAYER_CHARACTER, "down")[0]);
    // charIndex 必须设置：playAnim 靠它算帧号，缺了会生成 NaN 帧、走路无动画
    this.player.setData("charIndex", PLAYER_CHARACTER);
    this.player.setData("dir", "down");
    this.player.setCollideWorldBounds(true);
    this.physics.world.setBounds(0, 0, this.worldWidth, this.worldHeight);
    for (const l of objLayers) this.physics.add.collider(this.player, l);

    // —— 相机 ——
    this.cameras.main.setBounds(0, 0, this.worldWidth, this.worldHeight);
    this.cameras.main.setZoom(CAMERA_ZOOMS[this.zoomIndex]);
    this.cameras.main.startFollow(this.player, true, 0.1, 0.1);

    this.cursors = this.input.keyboard!.createCursorKeys();

    // —— HUD（DOM）——
    this.hud = new Hud({
      onSend: (text) => this.sendChat(text),
      onClose: () => {
        this.chatTarget = null;
      },
    });

    // 状态栏：大字号 + scale 抵消 zoom，保证清晰；深度压过一切角色
    this.statusText = this.add
      .text(0, 0, "连接中…", {
        fontSize: "24px",
        color: "#1a2f0e",
        backgroundColor: "rgba(255,248,231,0.8)",
      })
      .setDepth(HUD_DEPTH);

    // —— 暖色滤镜（mage3 石城 → 治愈系明度带的轻量拉拢，不动素材）——
    // B 键开关，方便 A/B 对比决定 W4 是否换绿色系底图。Canvas 回退时静默跳过。
    if (this.game.renderer.type === Phaser.WEBGL) {
      const fx = this.cameras.main.filters.internal.addColorMatrix();
      // 注意：brightness 是乘法（0=黑，1=原图）——提亮要传 >1 的值
      fx.colorMatrix.brightness(1.12); // 提亮 12%（重置矩阵后应用）
      fx.colorMatrix.saturate(1.2, true); // 加饱和（乘法叠加）
      fx.colorMatrix.hue(-12, true); // 色相向暖微偏
      this.warmFilter = fx;
    } else {
      console.warn("当前渲染器不支持 Filters，暖色滤镜已跳过");
    }
    this.input.keyboard!.on("keydown-B", () => this.toggleWarm());

    // —— 视角切换：键 1/2/3 ——
    for (let i = 0; i < ZOOM_KEYS.length; i++) {
      const key = ZOOM_KEYS[i];
      this.input.keyboard!.on(`keydown-${key}`, () => this.setZoom(i));
    }

    // Enter 打开对话；Esc 关闭（输入框内部也各自处理 Esc）
    this.input.keyboard!.on("keydown-ENTER", () => this.tryOpenChat());
    this.input.keyboard!.on("keydown-ESC", () => this.hud.closeChat());

    this.net = new NetClient(
      "ws://localhost:8000/ws",
      (msg) => this.onServerMessage(msg),
      () => this.refreshStatusText(),
    );
    this.net.connect();
  }

  update(time: number): void {
    const dt = Math.min((time - this.lastFrameTime) / 1000, 0.1); // 上限防切页回来跳变
    this.lastFrameTime = time;

    // 状态栏跟随相机可视区左上角；scale 抵消 zoom（zoom 可切换，动态取值）
    const zoom = this.cameras.main.zoom;
    const view = this.cameras.main.worldView;
    this.statusText.setPosition(view.x + 6 / zoom, view.y + 6 / zoom);
    this.statusText.setScale(1 / zoom);

    // 居民插值永远推进——对话打开时世界照常活着
    this.stepResidents(dt);
    this.player.setDepth(this.player.y);

    // 对话打开时不动
    if (this.hud.isChatOpen) {
      this.player.setVelocity(0, 0);
      this.playAnim(this.player, "idle");
      this.hud.setHint(null);
      return;
    }

    let dx = 0;
    let dy = 0;
    if (this.cursors.left.isDown) dx -= 1;
    if (this.cursors.right.isDown) dx += 1;
    if (this.cursors.up.isDown) dy -= 1;
    if (this.cursors.down.isDown) dy += 1;

    if (dx !== 0 || dy !== 0) {
      const len = Math.hypot(dx, dy);
      this.player.setVelocity((dx / len) * PLAYER_SPEED, (dy / len) * PLAYER_SPEED);
      const dir = this.dirFromInput(dx, dy);
      this.playAnim(this.player, "walk", dir);
      if (time - this.lastReportAt > MOVE_REPORT_INTERVAL_MS) {
        this.net.send("player_move", { x: Math.round(this.player.x), y: Math.round(this.player.y) });
        this.lastReportAt = time;
      }
    } else {
      this.player.setVelocity(0, 0);
      this.playAnim(this.player, "idle");
    }

    // 走近提示：最近的居民在对话范围内（附当前动作，让"正在干嘛"近看也有）
    const nearest = this.nearestResident();
    if (
      nearest &&
      Phaser.Math.Distance.Between(this.player.x, this.player.y, nearest.sprite.x, nearest.sprite.y) <= CHAT_RANGE_PX
    ) {
      if (this.chattingIds.has(nearest.id)) {
        this.hud.setHint(`按 Enter 加入 ${nearest.name} 的对话`);
      } else {
        const doing = nearest.action ? `（正在${nearest.action}）` : "";
        this.hud.setHint(`按 Enter 和 ${nearest.name} 说话${doing}`);
      }
    } else {
      this.hud.setHint(null);
    }
  }

  // ---------- 相机 / 滤镜 ----------

  private setZoom(index: number): void {
    this.zoomIndex = index;
    this.cameras.main.setZoom(CAMERA_ZOOMS[index]);
    this.refreshStatusText();
  }

  private toggleWarm(): void {
    if (!this.warmFilter) return;
    this.warmFilter.active = !this.warmFilter.active;
    this.warmOn = this.warmFilter.active;
    this.refreshStatusText();
  }

  private refreshStatusText(): void {
    const warm = this.warmFilter ? (this.warmOn ? " · ☀️" : "") : "";
    const label = this.gameTimeLabel || "连接中…";
    this.statusText.setText(`小镇时间: ${label} · 视角×${CAMERA_ZOOMS[this.zoomIndex]}${warm}`);
  }

  // ---------- 居民插值 ----------

  /** 每帧把居民向服务端目标点逼近（详见文件头"按时到位"说明）+ y-sort + 名牌跟随 */
  private stepResidents(dt: number): void {
    for (const rv of this.residents.values()) {
      const { sprite } = rv;
      const dx = rv.target.x - sprite.x;
      const dy = rv.target.y - sprite.y;
      const dist = Math.hypot(dx, dy);
      if (dist <= RESIDENT_ARRIVE_EPSILON) {
        if (rv.moving) {
          this.playAnim(sprite, "idle");
          rv.moving = false;
        }
      } else {
        const speed = Math.min(dist * SERVER_TICKS_PER_SECOND, RESIDENT_MAX_SPEED);
        const step = Math.min(dist, speed * dt);
        sprite.x += (dx / dist) * step;
        sprite.y += (dy / dist) * step;
        // playAnim 内部有"同动画不重播"判断，方向变了才切换
        this.playAnim(sprite, "walk", this.dirFromInput(dx, dy));
        rv.moving = true;
      }
      // 深度即 y：脚下越靠下越靠前（名牌比本人高 1 层，被前方角色盖住是正确的）
      sprite.setDepth(sprite.y);
      rv.label.setPosition(sprite.x, sprite.y - LABEL_OFFSET_Y);
      rv.label.setDepth(sprite.y + 1);
    }
  }

  // ---------- 动画（folk 精灵表，帧号由 mapData.walkFrames 计算） ----------

  private dirFromInput(dx: number, dy: number): Direction {
    if (Math.abs(dx) >= Math.abs(dy)) return dx < 0 ? "left" : "right";
    return dy < 0 ? "up" : "down";
  }

  private playAnim(
    sprite: Phaser.GameObjects.Sprite,
    mode: "walk" | "idle",
    dir?: Direction,
  ): void {
    const charIndex = sprite.getData("charIndex") as number;
    const direction = dir ?? (sprite.getData("dir") as Direction | undefined) ?? "down";
    if (dir) sprite.setData("dir", direction);
    const frames = walkFrames(charIndex, direction);
    if (mode === "idle") {
      sprite.anims.stop();
      sprite.setFrame(frames[0]);
    } else {
      const key = `walk-${charIndex}-${direction}`;
      if (!this.anims.exists(key)) {
        this.anims.create({
          key,
          frames: frames.map((f) => ({ key: "folk", frame: f })),
          frameRate: 8,
          repeat: -1,
        });
      }
      if (sprite.anims.currentAnim?.key !== key) sprite.anims.play(key);
    }
  }

  // ---------- 居民 ----------

  private nearestResident(): ResidentVisual | null {
    let best: ResidentVisual | null = null;
    let bestDist = Infinity;
    for (const rv of this.residents.values()) {
      const d = Phaser.Math.Distance.Squared(this.player.x, this.player.y, rv.sprite.x, rv.sprite.y);
      if (d < bestDist) {
        bestDist = d;
        best = rv;
      }
    }
    return best;
  }

  private tryOpenChat(): void {
    if (this.hud.isChatOpen) return;
    const nearest = this.nearestResident();
    if (!nearest) return;
    const dist = Phaser.Math.Distance.Between(this.player.x, this.player.y, nearest.sprite.x, nearest.sprite.y);
    if (dist > CHAT_RANGE_PX) return;
    this.chatTarget = nearest.id;
    const groupChat = this.chattingIds.has(nearest.id);
    this.hud.openChat(nearest.id, groupChat ? `加入 ${nearest.name} 的对话` : `和 ${nearest.name} 聊天`);
  }

  private sendChat(text: string): void {
    if (this.chatTarget) {
      this.net.send("player_chat", { resident_id: this.chatTarget, text });
    }
  }

  private onServerMessage(msg: ServerMessage): void {
    if (msg.type === "world_state") {
      this.gameTimeLabel = String(msg.payload.game_time);
      this.refreshStatusText();
      const residents = (msg.payload.residents ?? []) as Array<Record<string, unknown>>;
      for (const r of residents) this.syncResident(r);
    } else if (msg.type === "chat_reply") {
      const id = String(msg.payload.resident_id);
      this.hud.hideThinking();
      // 群聊可能多句，逐句入面板；一对一仍是单句
      const lines = (msg.payload.lines ?? []) as Array<[string, string]>;
      for (const [speaker, text] of lines) {
        this.hud.addChatLine(id, speaker, text);
      }
    } else if (msg.type === "event_log") {
      this.hud.addEvent(String(msg.payload.game_time), String(msg.payload.text));
    } else if (msg.type === "error") {
      const code = String(msg.payload.code ?? "");
      if (code === "too_far") this.hud.setHint("太远了，走过去再说");
      else console.warn("server error:", msg.payload);
    }
  }

  /**
   * 按服务端状态同步一个居民：只更新插值目标与头顶信息；
   * 位置由 update() 的 stepResidents 平滑逼近（不再直接 setPosition 跳格）。
   */
  private syncResident(r: Record<string, unknown>): void {
    const id = String(r.id);
    const name = String(r.name);
    const x = Number(r.x);
    const y = Number(r.y);
    const action = String(r.action ?? "");
    const occupation = String(r.occupation ?? "");
    const chatting = Boolean(r.chatting);

    let rv = this.residents.get(id);
    if (!rv) {
      // 首次出现：直接落位（没有"从 (0,0) 走过来"可言）
      const charIndex = CHARACTER_INDEX[id] ?? 0;
      const sprite = this.add.sprite(x, y, "folk", walkFrames(charIndex, "down")[0]);
      sprite.setData("charIndex", charIndex);
      sprite.setData("dir", "down");
      // 头顶名牌：大字号 + scale 缩小，跟随居民世界坐标
      const label = this.add
        .text(x, y - LABEL_OFFSET_Y, name, { fontSize: "18px", color: "#1a2f0e", backgroundColor: "rgba(255,248,231,0.7)" })
        .setOrigin(0.5, 1)
        .setScale(0.5);
      rv = { id, name, sprite, label, target: { x, y }, action, emoji: "", moving: false };
      this.residents.set(id, rv);
    } else if (Phaser.Math.Distance.Between(rv.sprite.x, rv.sprite.y, x, y) > RESIDENT_SNAP_DISTANCE) {
      // 断线重连/瞬移：直接贴齐，不插值（否则居民会横穿全图）
      rv.sprite.setPosition(x, y);
      rv.target = { x, y };
    } else {
      rv.target = { x, y };
    }

    if (chatting) this.chattingIds.add(id);
    else this.chattingIds.delete(id);

    // 头顶 emoji：动作文本 → emoji（变了才重设文字纹理）
    const emoji = actionEmoji(action, occupation);
    if (emoji !== rv.emoji) {
      rv.emoji = emoji;
      rv.label.setText(`${name} ${emoji}`);
    }
    rv.action = action;
  }
}
