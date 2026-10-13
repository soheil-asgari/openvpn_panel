<div dir="rtl">

# مرجع خط فرمان

دو نقطهٔ ورود نصب میشود: منوی مدیریت (`<slug>`) و CLI پایتونی برای کارهای
سرویسی. دستورهایی که با دیتابیس یا اسرار کار میکنند باید با کاربر سرویس یا root
اجرا شوند.

## منوی مدیریت — `<slug>`

| فرمان | توضیح |
| --- | --- |
| `<slug>` | منوی تعاملی |
| `<slug> show` | نمایش اطلاعات ورود و دسترسی |
| `<slug> set-port PORT` | تغییر پورت پنل (unit و فایروال بهروز میشود) |
| `<slug> set-user NAME` | تغییر نام کاربری مدیر |
| `<slug> set-password [PW]` | تغییر گذرواژهٔ مدیر (بدون PW تصادفی) |
| `<slug> add-instance SPEC` | افزودن نمونهٔ OpenVPN |
| `<slug> remove-instance NAME` | حذف نمونهٔ OpenVPN |
| `<slug> restart` | راهاندازی مجدد سرویسها |
| `<slug> update` | بهروزرسانی |
| `<slug> uninstall [--purge]` | حذف |

## CLI سرویس — `<slug>` (پایتون)

| فرمان | توضیح |
| --- | --- |
| `init-db` | ساخت/ارتقای اسکیمای دیتابیس |
| `create-admin USER [--password PW]` | ساخت مدیر اولیه |
| `reset-password USER [--password PW]` | بازنشانی گذرواژه |
| `gen-secrets --output FILE --database-url URL` | نوشتن اسرار (حالت ۶۰۰) |
| `import-auth` | برونبری کاربران به فایل احراز هویت OpenVPN |
| `backup [--output DIR] [--no-pki]` | ساخت پشتیبان رمزشده |
| `restore ARCHIVE [--configs]` | بازگردانی از آرشیو رمزشده |
| `migrate-db --target URL` | انتقال دادهها به دیتابیس دیگر |
| `tunnel-list` | فهرست تونلها |
| `tunnel-add ...` | ساخت و راهاندازی تونل |
| `tunnel-action NAME ACTION` | وضعیت/راهاندازی مجدد/بهروزرسانی/حذف تونل |
| `tunnel-mtu NAME [--apply]` | یافتن MTU (و اعمال اختیاری) |

## ابزارهای بررسی

| اسکریپت | کاربرد |
| --- | --- |
| `scripts/vps-smoke-test.sh` | آزمون دود پس از نصب (گزارش پاکسازیشده) |
| `scripts/post-reboot-check.sh` | بررسی پس از راهاندازی مجدد |
| `scripts/tunnel-check.sh` | بررسی انتها-به-انتها تونل |
| `scripts/seed_demo.py` | تولید دادهٔ نمایشی ساختگی |

</div>
