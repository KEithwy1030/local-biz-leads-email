# local-biz-leads-email STATUS
更新：2026-10-19 19:12 HKT

## 当前进度
| 项 | 状态 |
|----|------|
| build | **0.1.7** SUCCEEDED (`JuXqTJDD6CJivMvcY`) |
| Store | https://apify.com/keiwy/local-biz-leads-email |
| prefill | ✅ query=dentist / location=Austin, TX / maxItems=5（防 UNDER_MAINTENANCE）|
| onlyWithEmail | ✅ input + 过滤 + README；云测 run `8qXFOz9KZMxGziYhH` 6/6 有 email |
| 蓝海文案 | OSM / No Maps API / $1.50/1k（0.1.3 起）|

## 上轮
- 0.1.7：maxItems default 5→20（prefill 仍 5）；空 `{}` 验跑 `yKMBSGfChGZaH1xyn` SUCCEEDED 13.5s dataset 19
- 0.1.6：required 字段补 default（query=dentist/location=Austin, TX/maxItems=5），空 `{}` 验跑 `Q8CSKhUliahY5Y2oR` SUCCEEDED dataset 5
- 0.1.5：input schema prefill；验跑 `cOIZTjXNmP6JSpaHY` dataset 5
- 0.1.4：onlyWithEmail
- 0.1.3：Store 蓝海 SEO/README/实测表
