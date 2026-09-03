// 标题画面：进镇前的第一触点（品牌感 + 入口 + 设置）。
//
// 设计说明（为什么这样设计）：
// - 纯 Phaser 绘制不依赖任何新素材：深夜→黎明的纵向渐变 + 程序生成的萤火
//   粒子（Graphics 画圆点注册成纹理），像素字体标题——零资产成本的品牌画面。
// - 菜单用 Phaser Text + setInteractive（悬停高亮），键盘 Enter/Space 同样可进；
//   「设置」打开的是 settings.ts 的 DOM 面板（和游戏内 ⚙ 共用一个）。
// - RESIZE 模式下窗口可随时变化：全部 UI 收进 Container，resize 事件只重排
//   容器位置，内部相对布局不动。
// - 进镇转场：fadeOut 400ms 后 scene.start("TownScene")；starting 标志防重复。

import Phaser from "phaser";
import { PIXEL_FONT_FAMILY } from "../ui/font";
import { toggleSettingsPanel } from "../settings";

export class TitleScene extends Phaser.Scene {
  private root!: Phaser.GameObjects.Container;
  private bg: Phaser.GameObjects.Graphics | null = null;
  private starting = false;

  constructor() {
    super("Title");
  }

  create(): void {
    this.starting = false;
    const { width, height } = this.scale;

    // 1) 背景：深夜（上）→ 黎明草绿（下）的纵向渐变
    const bg = this.add.graphics();
    bg.fillGradientStyle(0x0c1a10, 0x0c1a10, 0x1f3a14, 0x1f3a14, 1);
    bg.fillRect(0, 0, width, height);
    bg.setScrollFactor(0).setDepth(-10);
    this.bg = bg;

    // 2) 萤火粒子：程序生成柔光圆点纹理，缓慢上浮 + 渐隐
    if (!this.textures.exists("firefly")) {
      const g = this.add.graphics();
      g.fillStyle(0xfff3b0, 1);
      g.fillCircle(4, 4, 3);
      g.generateTexture("firefly", 8, 8);
      g.destroy();
    }
    this.add
      .particles(0, 0, "firefly", {
        x: { min: 0, max: width },
        y: height + 10,
        lifespan: 9000,
        speedY: { min: -14, max: -30 },
        speedX: { min: -6, max: 6 },
        scale: { start: 0.5, end: 1.1 },
        alpha: { start: 0.9, end: 0 },
        quantity: 1,
        frequency: 700,
        blendMode: Phaser.BlendModes.ADD,
      })
      .setScrollFactor(0)
      .setDepth(-5);

    // 3) UI 根容器（resize 只挪它）
    this.root = this.add.container(width / 2, height / 2);

    const title = this.add
      .text(0, -110, "AI 小镇", {
        fontFamily: `"${PIXEL_FONT_FAMILY}", "PingFang SC", sans-serif`,
        fontSize: "88px",
        color: "#fff8e7",
        stroke: "#0c1a10",
        strokeThickness: 8,
      })
      .setOrigin(0.5);
    const subtitle = this.add
      .text(0, -36, "七个居民各自生活的小镇，故事每天都不一样", {
        fontFamily: `"${PIXEL_FONT_FAMILY}", "PingFang SC", sans-serif`,
        fontSize: "18px",
        color: "#cfe3b8",
      })
      .setOrigin(0.5);

    const start = this.menuButton("走进小镇", -40, () => this.enterTown());
    const settings = this.menuButton("设　置", 28, () => toggleSettingsPanel());
    const hint = this.add
      .text(0, 130, "方向键移动 · Enter 对话 · 1/2/3 切换视角", {
        fontFamily: `"${PIXEL_FONT_FAMILY}", "PingFang SC", sans-serif`,
        fontSize: "14px",
        color: "#9db884",
      })
      .setOrigin(0.5);

    this.root.add([title, subtitle, start, settings, hint]);

    // 4) 键盘入口：Enter / Space 直接进镇
    this.input.keyboard?.on("keydown-ENTER", () => this.enterTown());
    this.input.keyboard?.on("keydown-SPACE", () => this.enterTown());

    // 5) RESIZE：重画背景 + 重排根容器
    const reflow = (): void => {
      this.bg?.clear();
      this.bg?.fillGradientStyle(0x0c1a10, 0x0c1a10, 0x1f3a14, 0x1f3a14, 1);
      this.bg?.fillRect(0, 0, this.scale.width, this.scale.height);
      this.root.setPosition(this.scale.width / 2, this.scale.height / 2);
    };
    this.scale.on("resize", reflow);
    this.events.once("shutdown", () => {
      this.scale.off("resize", reflow);
    });

    this.cameras.main.fadeIn(600, 0, 0, 0);
  }

  /** 菜单按钮：像素字体文本，悬停高亮 + 指针手型 */
  private menuButton(label: string, y: number, onTap: () => void): Phaser.GameObjects.Text {
    const btn = this.add
      .text(0, y, label, {
        fontFamily: `"${PIXEL_FONT_FAMILY}", "PingFang SC", sans-serif`,
        fontSize: "30px",
        color: "#e8f2d9",
        stroke: "#0c1a10",
        strokeThickness: 5,
      })
      .setOrigin(0.5)
      .setInteractive({ useHandCursor: true });
    btn.on("pointerover", () => btn.setColor("#ffffff"));
    btn.on("pointerout", () => btn.setColor("#e8f2d9"));
    btn.on("pointerup", onTap);
    return btn;
  }

  /** 进镇：防重复触发 + 淡出转场 */
  private enterTown(): void {
    if (this.starting) return;
    this.starting = true;
    this.cameras.main.fadeOut(400, 0, 0, 0);
    this.cameras.main.once("camerafadeoutcomplete", () => {
      this.scene.start("TownScene");
    });
  }
}
