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
  width: 960,
  height: 640,
  parent: "game",
  backgroundColor: "#2d5016",
  pixelArt: true,
  physics: {
    default: "arcade",
    arcade: { debug: false },
  },
  scene: [TownScene],
});
