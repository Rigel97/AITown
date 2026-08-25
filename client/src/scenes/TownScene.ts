// 主小镇场景（Phaser v4）—— v2 视觉：LimeZu 系素材 + 整 Sprite 建筑 + 昼夜光照
// 设计说明（为什么这样设计）：
// - 地图从共享 town_map.json 读取（与服务端寻路同源）：v2 格式 = 2 层地面
//   tilemap（地面 + 草地变种贴花）+ props 整 Sprite（建筑/树木/道具）。
//   建筑不再用瓦片拼墙：Sprite depth = 底边 y，角色可以走到树冠/屋顶
//   "后面"被正确遮挡（旧的 obj 瓦片层做不到）。
// - 阻挡：props 的 bw×bh 足迹 → 每件一个隐形 static body + collider；
//   足迹与服务端 walkable 网格同源（build_map.py 一个来源推导），
//   客户端物理体与服务端寻路永远不会打架。
// - 资产两阶段加载：preload 只装 JSON；create 读出 tileset/props 清单后
//   动态发起第二阶段加载（图片文件名在 JSON 里，写死会两处维护），
//   完成后再 buildWorld——net 连接也等世界就绪，避免 world_state 先到
//   而 residents 容器未建。
// - 角色用 folk2 精灵表（每角色 7 帧 × 4 向，帧 32×64：角色 1 格宽 2 格高，
//   更接近真人比例）。origin (0.5, 0.75)：精灵 y 仍是"逻辑格中心"（服务端
//   坐标语义不变），脚底落在格底边；物理体只覆盖脚部 20×20。
// - 相机 zoom 可切换（键 1/2/3）：近身逛街 ×2 与"上帝视角"×1 之间的观感
//   差异很大，留给玩家自己选；为让文字清晰，文字一律"大字号渲染 + scale
//   缩小"——小纹理被 zoom 放大会糊，大纹理缩放后才锐利。
// - 居民移动是插值的：服务端每 1/3 秒广播一个单格目标点，客户端每帧
//   "按时到位"逼近（速度 = 剩余距离 × 广播频率）——平滑连续、不切墙角、
//   转向精确（2026-08-19 视觉优化，修掉逐秒 96px 跳变）。
// - 头顶名牌 = 名字 + 动作 emoji（actionEmoji 关键词映射，斯坦福
//   pronunciato 的低成本版），让"正在干嘛"扫一眼可见。
// - 深度即 y：所有角色（玩家/居民/名牌/道具）depth = y——脚下越靠下越靠前，
//   聚集点（餐馆、广场）居民互相遮挡才正确。
// - 脚下椭圆阴影：角色"贴地"的关键细节，跟随角色、depth 比本人低 1。
// - 昼夜光照：按游戏时钟分 5 档调相机 ColorMatrix（清晨/白天/黄昏/入夜/
//   深夜），只在档位变化时重设矩阵（不逐帧上传）。B 键可整体关闭对照。
// - 文字 UI（对话卡/事件日志/输入）走 DOM HUD：对话是"立绘+名牌+台词"的
//   一体化卡片（见 ui/hud.ts），画布只画像素世界（头顶名牌）。
// - 对话打开时禁用移动，避免边打字边走路。

import Phaser from "phaser";
import { NetClient, type ServerMessage } from "../net/client";
import { Hud } from "../ui/hud";
import { actionEmoji } from "../world/actionEmoji";
import {
  CHARACTER_INDEX,
  FOLK_FRAME_H,
  FOLK_FRAME_W,
  PLAYER_CHARACTER,
  idleFrame,
  propAnchorPx,
  propFootprint,
  walkFrames,
  withinChebyshevTiles,
  type Direction,
  type TownMapData,
} from "../world/mapData";

/** 相机缩放档位（键 1/2/3 直达）。×1 一屏 30×20 格，接近斯坦福"上帝视角" */
const CAMERA_ZOOMS = [2, 1.5, 1] as const;
const ZOOM_KEYS = ["ONE", "TWO", "THREE"] as const;
const DEFAULT_ZOOM_INDEX = 0;
const MOVE_REPORT_INTERVAL_MS = 100;
// 与服务端 engine.CHAT_RANGE_TILES 对齐：瓦片切比雪夫距离，同一套几何
// （旧版用像素欧氏距离，边界处与服务端判定错位，出现"有提示却 too_far"）
const CHAT_RANGE_TILES = 3;
const PLAYER_SPEED = 110; // 像素/秒

// —— 居民插值参数 ——
// 服务端 3 拍/秒广播单格目标点（见 engine.BROADCAST_INTERVAL_SECONDS）。
// "按时到位"：速度 = 剩余距离 × 每秒拍数——正好在下一拍到达，既不会提前
// 到位后停顿（每格一顿的脉冲感），也永不清零剩余距离后的僵直。
const SERVER_TICKS_PER_SECOND = 3;
const RESIDENT_MAX_SPEED = 240; // 上限：重连/大偏移时也别让居民"冲刺"
const RESIDENT_ARRIVE_EPSILON = 0.5; // 剩余距离小于此值视为已到达

// 名牌在头顶上方：帧高 64、origin 0.75 → 头顶在 y-48，名牌再抬高 4px
const LABEL_OFFSET_Y = 52;
// 阴影贴在脚底（格底边 = y+16），略上收 2px 视觉更贴
const SHADOW_OFFSET_Y = 14;
const HUD_DEPTH = 99999; // 状态栏永远在最上层（角色 depth = y 最大约 1300）

// —— 昼夜光照档（brightness 是乘法：>1 提亮 <1 压暗；hue 单位度，负=暖）——
// 只在档位切换时重设矩阵；参数手感来自 mage3 时代暖色滤镜的白昼校准值外推
const LIGHT_SCHEDULE: ReadonlyArray<{
  from: number; // 含
  to: number; // 不含
  brightness: number;
  saturate: number;
  hue: number;
  icon: string;
  name: string;
}> = [
  { from: 6, to: 8, brightness: 1.06, saturate: 1.18, hue: -10, icon: "🌅", name: "清晨" },
  { from: 8, to: 17, brightness: 1.12, saturate: 1.2, hue: -12, icon: "☀️", name: "白天" },
  { from: 17, to: 19, brightness: 1.04, saturate: 1.28, hue: -20, icon: "🌇", name: "黄昏" },
  { from: 19, to: 21, brightness: 0.92, saturate: 1.05, hue: -10, icon: "🌆", name: "入夜" },
  { from: 21, to: 24, brightness: 0.74, saturate: 0.85, hue: 22, icon: "🌙", name: "深夜" },
  { from: 0, to: 6, brightness: 0.74, saturate: 0.85, hue: 22, icon: "🌙", name: "深夜" },
];

/** 一个居民的客户端视觉状态：精灵 + 阴影 + 名牌 + 插值目标（数据收口） */
interface ResidentVisual {
  id: string;
  name: string;
  sprite: Phaser.GameObjects.Sprite;
  shadow: Phaser.GameObjects.Image;
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
  private playerShadow!: Phaser.GameObjects.Image;
  private cursors!: Phaser.Types.Input.Keyboard.CursorKeys;
  private net!: NetClient;
  private statusText!: Phaser.GameObjects.Text;
  private hud!: Hud;
  private lastReportAt = 0;
  private lastFrameTime = 0;
  private residents = new Map<string, ResidentVisual>();
  private chattingIds = new Set<string>();
  private chatTarget: string | null = null;
  private tileDim = 32;
  private worldWidth = 0;
  private worldHeight = 0;
  private zoomIndex = DEFAULT_ZOOM_INDEX;
  private warmFilter: Phaser.Filters.ColorMatrix | null = null;
  private warmOn = true;
  private gameTimeLabel = "";
  /** 首次 world_state 是否已把玩家贴齐到服务端位置（之后客户端权威，忽略） */
  private playerSynced = false;
  /** v2 两阶段加载：props/tileset 就绪前 update/网络消息全部让路 */
  private worldReady = false;
  private lightBucket = -1;

  constructor() {
    super("TownScene");
  }

  preload(): void {
    // 只装地图 JSON：tileset/props 文件名要从 JSON 里读，第二阶段再装
    this.load.json("town-map", "assets/town_map.json");
  }

  create(): void {
    const townMap = this.cache.json.get("town-map") as TownMapData;
    if (townMap.version !== 2) {
      throw new Error(`town_map.json 版本不符（${townMap.version}），请重跑 client/scripts/build_map.py`);
    }
    // —— 第二阶段加载：地图声明的全部图片 ——
    this.load.image("town-tiles", townMap.tileset);
    this.load.spritesheet("folk", "assets/v2/folk2.png", {
      frameWidth: FOLK_FRAME_W,
      frameHeight: FOLK_FRAME_H,
    });
    this.load.image("shadow", "assets/v2/shadow.png");
    const propImgs = new Set(townMap.props.map((p) => p.img));
    for (const img of propImgs) {
      this.load.image(`prop-${img}`, `assets/v2/props/${img}.png`);
    }
    this.load.once(Phaser.Loader.Events.COMPLETE, () => this.buildWorld(townMap));
    this.load.start();
  }

  /** 世界装配：v2 地面 tilemap + props 道具层 + 角色 + 相机/HUD/网络 */
  private buildWorld(townMap: TownMapData): void {
    this.tileDim = townMap.tileDim;
    this.worldWidth = townMap.cols * this.tileDim;
    this.worldHeight = townMap.rows * this.tileDim;

    // —— 地面 tilemap（bg0 地面 / bg1 草地变种贴花；不挂碰撞）——
    for (const layerData of townMap.bgLayers) {
      const map = this.make.tilemap({
        data: layerData,
        tileWidth: this.tileDim,
        tileHeight: this.tileDim,
      });
      const tiles = map.addTilesetImage("town-tiles");
      if (!tiles) throw new Error("tileset 加载失败");
      const layer = map.createLayer(0, tiles, 0, 0);
      if (!layer) throw new Error("layer 创建失败");
    }

    // —— props 道具层：建筑/树木整 Sprite，depth=底边；足迹 → 隐形碰撞体 ——
    const obstacles: Phaser.GameObjects.Rectangle[] = [];
    for (const p of townMap.props) {
      const anchor = propAnchorPx(p, this.tileDim);
      const depth = (p.row + 1) * this.tileDim;
      const img = this.add.image(anchor.x, anchor.y, `prop-${p.img}`).setOrigin(0.5, 1);
      const foot = propFootprint(p);
      if (foot) {
        img.setDepth(depth);
        const cx = ((foot.c0 + foot.c1 + 1) / 2) * this.tileDim;
        const cy = ((foot.r0 + foot.r1 + 1) / 2) * this.tileDim;
        const rect = this.add
          .rectangle(
            cx,
            cy,
            (foot.c1 - foot.c0 + 1) * this.tileDim,
            (foot.r1 - foot.r0 + 1) * this.tileDim,
          )
          .setVisible(false);
        this.physics.add.existing(rect, true); // static body
        obstacles.push(rect);
      } else {
        // 贴花（花坛/碎石）：贴地面，任何角色/道具都能盖住它
        img.setDepth(p.row * this.tileDim + 1);
      }
    }

    // —— 玩家 ——
    const pc = townMap.playerSpawn.col * this.tileDim + this.tileDim / 2;
    const pr = townMap.playerSpawn.row * this.tileDim + this.tileDim / 2;
    this.player = this.physics.add.sprite(pc, pr, "folk", idleFrame(PLAYER_CHARACTER, "down"));
    // origin (0.5, 0.75)：y 语义 = 逻辑格中心（服务端坐标），脚底落格底边
    this.player.setOrigin(0.5, 0.75);
    // 物理体只覆盖脚部 20×20（帧 32×64 的右下区域）：头顶不参与撞墙
    const body = this.player.body;
    if (body) {
      body.setSize(20, 20);
      body.setOffset((FOLK_FRAME_W - 20) / 2, FOLK_FRAME_H - 20 - 4);
    }
    this.player.setData("charIndex", PLAYER_CHARACTER);
    this.player.setData("dir", "down");
    this.player.setCollideWorldBounds(true);
    this.physics.world.setBounds(0, 0, this.worldWidth, this.worldHeight);
    for (const o of obstacles) this.physics.add.collider(this.player, o);

    // 脚下阴影（比本人低 1 层，跟随移动）
    this.playerShadow = this.add
      .image(pc, pr + SHADOW_OFFSET_Y, "shadow")
      .setDepth(pr - 1);

    // —— 相机 ——
    this.cameras.main.setBounds(0, 0, this.worldWidth, this.worldHeight);
    this.cameras.main.setZoom(CAMERA_ZOOMS[this.zoomIndex]);
    this.cameras.main.startFollow(this.player, true, 0.1, 0.1);

    this.cursors = this.input.keyboard!.createCursorKeys();

    // —— HUD（DOM：探出式对话卡 + 事件日志）——
    this.hud = new Hud({
      onSend: (text) => this.sendChat(text),
      onClose: () => {
        this.chatTarget = null;
      },
      // 群聊台词只带说话人名字：用居民表反查 id（立绘/专属色都靠 id）。
      // 居民表随 world_state 异步到达，闭包里实时查而不是启动时快照
      resolveResident: (name) => {
        for (const rv of this.residents.values()) {
          if (rv.name === name) return rv.id;
        }
        return null;
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

    // —— 昼夜光照（相机级 ColorMatrix；Canvas 回退时静默跳过）——
    if (this.game.renderer.type === Phaser.WEBGL) {
      this.warmFilter = this.cameras.main.filters.internal.addColorMatrix();
      this.applyLight(8); // 默认白天档，首个 world_state 到达后按真实时间切档
    } else {
      console.warn("当前渲染器不支持 Filters，昼夜光照已跳过");
    }
    this.input.keyboard!.on("keydown-B", () => this.toggleLight());

    // —— 视角切换：键 1/2/3 ——
    for (let i = 0; i < ZOOM_KEYS.length; i++) {
      const key = ZOOM_KEYS[i];
      this.input.keyboard!.on(`keydown-${key}`, () => this.setZoom(i));
    }

    // Enter 打开对话；Esc 分层关闭（先记录浮层后对话卡）；H 开关对话记录
    // （H 仅输入框未聚焦时生效，主路径是 📜 按钮——打字优先于快捷键）
    this.input.keyboard!.on("keydown-ENTER", () => this.tryOpenChat());
    this.input.keyboard!.on("keydown-ESC", () => this.hud.escape());
    this.input.keyboard!.on("keydown-H", () => this.hud.toggleHistory());

    // 世界就绪后才连网络：world_state 到达时容器已建好（消息不会丢进空场景）
    this.net = new NetClient(
      "ws://localhost:8000/ws",
      (msg) => this.onServerMessage(msg),
      () => {
        // 重连（含首次连接）：状态栏刷新 + 清空全部"正在想…"占位符——
        // 断线期间在途的回复永远不会到达，占位符不清会永久挂死
        this.refreshStatusText();
        this.hud.hideThinking();
      },
    );
    this.net.connect();
    this.worldReady = true;
  }

  update(time: number): void {
    if (!this.worldReady) return;
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
    this.playerShadow.setPosition(this.player.x, this.player.y + SHADOW_OFFSET_Y);
    this.playerShadow.setDepth(this.player.y - 1);

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
        this.net.send("player_move", {
          x: Math.round(this.player.x),
          y: Math.round(this.player.y),
        });
        this.lastReportAt = time;
      }
    } else {
      this.player.setVelocity(0, 0);
      this.playAnim(this.player, "idle");
    }

    // 走近提示：最近的居民在对话范围内（附当前动作，让"正在干嘛"近看也有）
    const nearest = this.nearestResident();
    if (nearest && this.inChatRange(nearest)) {
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

  // ---------- 相机 / 光照 ----------

  private setZoom(index: number): void {
    this.zoomIndex = index;
    this.cameras.main.setZoom(CAMERA_ZOOMS[index]);
    this.refreshStatusText();
  }

  private toggleLight(): void {
    if (!this.warmFilter) return;
    this.warmFilter.active = !this.warmFilter.active;
    this.warmOn = this.warmFilter.active;
    this.refreshStatusText();
  }

  /** 按小时应用光照档；档位没变就跳过（不逐秒上传矩阵） */
  private applyLightByClock(label: string): void {
    const m = /(\d{2}):(\d{2})$/.exec(label);
    if (!m) return;
    this.applyLight(Number(m[1]));
  }

  private applyLight(hour: number): void {
    if (!this.warmFilter) return;
    const slot =
      LIGHT_SCHEDULE.find((s) => s.from <= hour && hour < s.to) ??
      LIGHT_SCHEDULE[LIGHT_SCHEDULE.length - 1];
    if (LIGHT_SCHEDULE.indexOf(slot) === this.lightBucket) return;
    this.lightBucket = LIGHT_SCHEDULE.indexOf(slot);
    const fx = this.warmFilter.colorMatrix;
    // brightness 是乘法（0=黑，1=原图）——重置矩阵后应用；饱和/色相乘法叠加
    fx.brightness(slot.brightness);
    fx.saturate(slot.saturate, true);
    fx.hue(slot.hue, true);
    this.refreshStatusText();
  }

  private currentLightIcon(): string {
    if (!this.warmFilter) return "";
    if (!this.warmOn) return "";
    const slot = this.lightBucket >= 0 ? LIGHT_SCHEDULE[this.lightBucket] : LIGHT_SCHEDULE[1];
    return ` · ${slot.icon}`;
  }

  private refreshStatusText(): void {
    const label = this.gameTimeLabel || "连接中…";
    this.statusText.setText(`小镇时间: ${label} · 视角×${CAMERA_ZOOMS[this.zoomIndex]}${this.currentLightIcon()}`);
  }

  // ---------- 居民插值 ----------

  /** 每帧把居民向服务端目标点逼近（详见文件头"按时到位"说明）+ y-sort + 名牌/阴影跟随 */
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
      rv.shadow.setPosition(sprite.x, sprite.y + SHADOW_OFFSET_Y);
      rv.shadow.setDepth(sprite.y - 1);
      rv.label.setPosition(sprite.x, sprite.y - LABEL_OFFSET_Y);
      rv.label.setDepth(sprite.y + 1);
    }
  }

  // ---------- 动画（folk2 精灵表：帧号由 mapData.idleFrame/walkFrames 计算） ----------

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
    if (mode === "idle") {
      sprite.anims.stop();
      sprite.setFrame(idleFrame(charIndex, direction));
    } else {
      const key = `walk-${charIndex}-${direction}`;
      if (!this.anims.exists(key)) {
        this.anims.create({
          key,
          frames: walkFrames(charIndex, direction).map((f) => ({ key: "folk", frame: f })),
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
      const d = Phaser.Math.Distance.Squared(
        this.player.x,
        this.player.y,
        rv.sprite.x,
        rv.sprite.y,
      );
      if (d < bestDist) {
        bestDist = d;
        best = rv;
      }
    }
    return best;
  }

  /** 与服务端同几何的对话距离判定（详见 mapData.withinChebyshevTiles）。
   * 用居民精灵插值位置算格：移动中可能与服务端权威格差 1 格，属可接受的瞬时误差。 */
  private inChatRange(resident: ResidentVisual): boolean {
    return withinChebyshevTiles(
      this.player.x,
      this.player.y,
      resident.sprite.x,
      resident.sprite.y,
      this.tileDim,
      CHAT_RANGE_TILES,
    );
  }

  private tryOpenChat(): void {
    if (this.hud.isChatOpen) return;
    const nearest = this.nearestResident();
    if (!nearest) return;
    if (!this.inChatRange(nearest)) return;
    this.chatTarget = nearest.id;
    this.hud.openChat(nearest.id, nearest.name);
  }

  private sendChat(text: string): void {
    if (this.chatTarget) {
      this.net.send("player_chat", { resident_id: this.chatTarget, text });
    }
  }

  private onServerMessage(msg: ServerMessage): void {
    if (!this.worldReady) return; // 资产加载期消息直接让路（连接在就绪后才开始，双保险）
    if (msg.type === "world_state") {
      this.gameTimeLabel = String(msg.payload.game_time);
      this.applyLightByClock(this.gameTimeLabel);
      this.refreshStatusText();
      // 首次同步：把玩家贴齐到服务端记忆的位置（读档/刷新页面不瞬移回出生点）。
      // 只做一次——之后是客户端权威移动，每秒广播的服务端镜像不再反写本地
      const p = msg.payload.player as { x?: unknown; y?: unknown } | undefined;
      if (!this.playerSynced && p && typeof p.x === "number" && typeof p.y === "number") {
        this.playerSynced = true;
        this.player.setPosition(p.x, p.y);
      }
      const residents = (msg.payload.residents ?? []) as Array<Record<string, unknown>>;
      for (const r of residents) this.syncResident(r);
    } else if (msg.type === "chat_reply") {
      const id = String(msg.payload.resident_id);
      // 群聊可能多句，逐句入面板（"正在想"占位符由 addChatLine 精确移除，
      // 不能全局清：同时对两人说话时，B 的回复不能误删 A 的占位）
      const lines = (msg.payload.lines ?? []) as Array<[string, string]>;
      for (const [speaker, text] of lines) {
        this.hud.addChatLine(id, speaker, text);
      }
    } else if (msg.type === "event_log") {
      this.hud.addEvent(String(msg.payload.game_time), String(msg.payload.text));
    } else if (msg.type === "save_ack") {
      const ok = Boolean(msg.payload.ok);
      const gameTime = String(msg.payload.game_time ?? "");
      this.hud.flashHint(ok ? `已存档（${gameTime}）` : "存档失败，稍后再试", 2500);
    } else if (msg.type === "error") {
      // 错误到达 = 这句话不会有回复了：占位符必须撤掉，
      // 否则"正在想…"永久挂死（旧版 too_far 就有这个问题）
      this.hud.hideThinking(this.chatTarget);
      const code = String(msg.payload.code ?? "");
      // flashHint：面板打开时 update 每帧 setHint(null)，普通 hint 会被
      // 立即盖掉，错误反馈必须走 TTL 临时通道
      if (code === "too_far") this.hud.flashHint("太远了，走过去再说", 2500);
      else if (code === "cooldown") this.hud.flashHint("话音刚落，缓一缓再说", 2500);
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
      const sprite = this.add.sprite(x, y, "folk", idleFrame(charIndex, "down"));
      sprite.setOrigin(0.5, 0.75); // 与玩家同一锚点语义：y = 逻辑格中心
      sprite.setData("charIndex", charIndex);
      sprite.setData("dir", "down");
      const shadow = this.add.image(x, y + SHADOW_OFFSET_Y, "shadow").setDepth(y - 1);
      // 头顶名牌：大字号 + scale 缩小，跟随居民世界坐标
      const label = this.add
        .text(x, y - LABEL_OFFSET_Y, name, {
          fontSize: "18px",
          color: "#1a2f0e",
          backgroundColor: "rgba(255,248,231,0.7)",
        })
        .setOrigin(0.5, 1)
        .setScale(0.5);
      rv = { id, name, sprite, shadow, label, target: { x, y }, action, emoji: "", moving: false };
      this.residents.set(id, rv);
    } else {
      const snapDist = this.tileDim * 4; // 超 4 格视为瞬移（重连），直接贴齐
      if (Phaser.Math.Distance.Between(rv.sprite.x, rv.sprite.y, x, y) > snapDist) {
        rv.sprite.setPosition(x, y);
        rv.target = { x, y };
      } else {
        rv.target = { x, y };
      }
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
