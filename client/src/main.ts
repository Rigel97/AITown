// Phaser 游戏入口与配置
// 设计说明：本文件只做游戏实例的装配，具体玩法全在 scenes/ 里。
// pixelArt: true 关闭纹理平滑，保证像素风清晰不模糊。
// 开发期把 game 挂到 window.__aitownGame（带类型），方便浏览器控制台/自动化调试。
import Phaser from "phaser";
import { PIXEL_FONT_FAMILY } from "./ui/font";
import { initSettingsPanel } from "./settings";
import { TitleScene } from "./scenes/TitleScene";
import { TownScene } from "./scenes/TownScene";
import "./style.css";

declare global {
  interface Window {
    __aitownGame?: Phaser.Game;
  }
}

/**
 * 启动前全量预载名牌像素字体的全部切片。
 * 为什么全量而不用 fonts.load(font, 文本) 按需预载：居民名在服务端，
 * 启动时不知道要预载哪些 unicode-range 切片；本地全量 ~1.1MB 毫秒级，
 * 之后任意名字都命中缓存。3s 超时兜底 + allSettled——个别切片失败
 * 只影响个别字形回退系统字体，绝不阻塞游戏启动。
 */
async function preloadPixelFont(): Promise<void> {
  const faces: Promise<FontFace>[] = [];
  document.fonts.forEach((face) => {
    if (face.family.replace(/"/g, "") === PIXEL_FONT_FAMILY) faces.push(face.load());
  });
  await Promise.race([
    Promise.allSettled(faces),
    new Promise((resolve) => setTimeout(resolve, 3000)),
  ]);
}

async function bootstrap(): Promise<void> {
  try {
    await preloadPixelFont();
  } catch (err) {
    console.warn("像素字体预载异常，名牌回退系统字体", err);
  }
  initSettingsPanel();
  window.__aitownGame = new Phaser.Game({
    type: Phaser.AUTO,
    parent: "game",
    // 画布跟随窗口（RESIZE）：同屏看到更多世界，而不是固定 960×640 在小窗里缩着。
    // 像素风语义不变——tile 仍 32px，zoom 决定屏幕放大倍数；高分屏由浏览器做
    // 整数倍位图放大，天然锐利（pixelArt 本身就是"一像素一像素"的放大观感）。
    scale: {
      mode: Phaser.Scale.RESIZE,
      width: "100%",
      height: "100%",
      autoCenter: Phaser.Scale.CENTER_BOTH,
      autoRound: true,
    },
    backgroundColor: "#2d5016",
    pixelArt: true,
    physics: {
      default: "arcade",
      arcade: { debug: false },
    },
    scene: [TitleScene, TownScene],
  });
}

void bootstrap();
