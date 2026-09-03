// 头顶名牌像素字体：Fusion Pixel 12px Proportional 简中版
// （字体本体 OFL 许可，npm 切片打包 MIT；来源 @vp-tw/cjk-web-fonts-fusion-pixel-font）。
// 引入即注册 @font-face——unicode-range 切片 woff2，浏览器按需下载分片。
// 注意：canvas 文本纹理只光栅化一次，名牌创建时字体必须已就绪（迟到不会
// 自动重画），所以 main.ts 启动时先全量预载再创建游戏实例。
import "@vp-tw/cjk-web-fonts-fusion-pixel-font/dist/12px/proportional/zh_hans/Fusion-Pixel-12px-Proportional-Simplified-Chinese.css";

/** 与切片 CSS 内 @font-face 声明一致的 font-family 名 */
export const PIXEL_FONT_FAMILY = "Fusion Pixel 12px Proportional Simplified Chinese";
