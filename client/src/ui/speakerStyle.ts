// 说话人样式：居民 id → 立绘路径与名牌专属色（纯数据 + 纯函数，可单测）。
//
// 设计说明（为什么这样设计）：
// - 名牌专属色按人设定档的职业/气质联想挑选（面包师暖棕、邮差藏青…），
//   群聊快速切换说话人时，玩家靠颜色 0.1 秒识别"现在是谁在说"。
// - 立绘文件名 = 居民 id（client/public/assets/portraits/{id}.png），
//   零映射表——新增居民只要放一张同名图即可，忘了放图则优雅降级为无立绘。
// - 玩家（residentId=null）用中性灰绿：你是"镇上的新面孔"，不与任何居民撞色。

export const PORTRAIT_DIR = "assets/portraits";

/** 居民 id → 名牌底色（V3 新居民，按职业/气质联想，与治愈系暖色调统一，深底 + 米字） */
export const SPEAKER_COLORS: Record<string, string> = {
  shen_qingwu: "#b46b3d", // 沈青梧：烘焙咖啡的暖棕
  murong_jin: "#3e5c76", // 慕容瑾：远洋船长的海雾蓝
  gao_xin: "#d95763", // 高新：驻唱歌手的烈焰珊瑚
  zhou_xingxing: "#8d6cab", // 周星星：画家的暮山紫
  li_suan: "#5f7d8c", // 李算：代码屏幕的灰蓝
  wu_wen: "#4e7a5a", // 吴文：老派教师的墨绿
  zheng_qiao: "#6b5340", // 郑巧：木匠的深棕
};

/** 玩家名牌色 */
export const PLAYER_COLOR = "#5e7d5a";
/** 未知名兜底色（理论上不会出现——服务端台词说话人都在居民名单内） */
export const FALLBACK_COLOR = "#6e6e6e";

/** 居民立绘 URL（public 静态资源，Vite 按相对路径服务） */
export function portraitUrl(residentId: string): string {
  return `${PORTRAIT_DIR}/${residentId}.png`;
}

/** 说话人名牌底色：null = 玩家；未知 id 用兜底色 */
export function speakerColor(residentId: string | null): string {
  if (residentId === null) return PLAYER_COLOR;
  return SPEAKER_COLORS[residentId] ?? FALLBACK_COLOR;
}
