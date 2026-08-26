// 走近提示的动作文案（Phase D：细粒度感知的 UI 端）。
//
// 服务端在居民站定时下发 near_object（身边家具名，见 server/world/objects.py）。
// 拼进提示时去重：action 已含物体名（"整理书架"）就不重复报"在书架旁"。

/** "（正在看书）" / "（在书架旁，正在看书）" / action 为空则空串 */
export function doingText(action: string, nearObject: string): string {
  if (!action) return "";
  if (nearObject && !action.includes(nearObject)) {
    return `（在${nearObject}旁，正在${action}）`;
  }
  return `（正在${action}）`;
}
