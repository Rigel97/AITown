// Phaser 游戏入口与配置
// 设计说明：本文件只做游戏实例的装配，具体玩法全在 scenes/ 里。
// pixelArt: true 关闭纹理平滑，保证像素风清晰不模糊。
// 开发期把 game 挂到 window.__aitownGame（带类型），方便浏览器控制台/自动化调试。
import Phaser from "phaser";
import { TownScene } from "./scenes/TownScene";
import "./style.css";

declare global {
  interface Window {
    __aitownGame?: Phaser.Game;
  }
}

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
  scene: [TownScene],
});
