# Online Auto Refresh Setup (Free)

## Trạng thái hiện tại

- Dashboard online: `https://stock-screening-dashboard-rust.vercel.app`
- Nút `Update` trên bản online đã chuyển sang lấy **giá live trực tiếp** (không cần backend local).

## Để bật full auto deploy từ GitHub (1 lần duy nhất)

1. Vào Vercel Account Settings -> Connections.
2. Add/Login GitHub connection cho account hiện tại.
3. Tạo project từ repo `anhkhoant94/tradingbot`.

## Vì sao vẫn cần bước trên

- Vercel API hiện trả lỗi khi link repo: chưa có GitHub Login Connection.
- Token GitHub hiện tại chưa có quyền ghi workflow (`.github/workflows`), nên chưa thể tạo cron bằng API.

## Khi đã đủ quyền

- Bật workflow `dashboard-auto-refresh.yml` để cron tự:
  1. cập nhật data live,
  2. rebuild dashboard,
  3. deploy production.
