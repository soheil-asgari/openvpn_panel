<div dir="rtl">

# نصب

آزمایششده روی اوبونتو ۲۲.۰۴ و ۲۴.۰۴؛ روی Debian 12 و خانوادهٔ RHEL و Alpine نیز
پشتیبانی میشود.

## نصب تکلاین (روش پیشنهادی)

روی سرور تازه، هیچ پیشنیازی لازم نیست:

```bash
bash <(curl -fsSL https://github.com/soheil-asgari/openvpn_panel/releases/latest/download/install.sh)
```

نصبکننده ابتدا **سلب مسئولیت حقوقی** را نشان میدهد؛ با تایپ `yes` (یا پرچم
`--yes`) ادامه دهید. سپس نسخهٔ تگشدهٔ همان انتشار را دانلود و SHA256 آن را از
`SHA256SUMS` بررسی میکند و در صورت عدم تطابق متوقف میشود. هرگز کد از شاخهٔ
`main` دریافت نمیشود.

## نصب غیرتعاملی

```bash
curl -fsSL https://github.com/soheil-asgari/openvpn_panel/releases/latest/download/install.sh \
  | sudo bash -s -- --yes \
      --user admin --password 'Aa1!aaaaAAAA' \
      --port 8080 --path /admin \
      --domain panel.example.com --email you@example.com
```

پرچمها: `--user --password --port --path --domain --email --yes`.

## زیرفرمانها

| فرمان | توضیح |
| --- | --- |
| `install.sh` | نصب یا تعمیر روی سرور تازه |
| `install.sh update` | بهروزرسانی به همان نسخه، بدون حذف دادهها |
| `install.sh repair` | بازاعمال unitها، دسترسیها و فایروال |
| `install.sh status` | نمایش وضعیت سرویسها و پنل |
| `install.sh uninstall [--purge]` | حذف؛ با `--purge` دادهها هم پاک میشوند |
| `install.sh --version` | نمایش نسخهٔ نصبکننده |

## پروکسی معکوس

پنل روی `127.0.0.1:<port>` گوش میدهد. با nginx یا Caddy جلوی آن TLS بگذارید و
`OVPN_TRUSTED_HOSTS=["panel.example.com"]` را در `panel.env` تنظیم کنید.

## مدیریت پس از نصب

نصبکننده یک منوی مدیریت در `/usr/local/bin/<slug>` نصب میکند:

```
sudo <slug>            # منوی تعاملی
sudo <slug> show       # نمایش اطلاعات ورود
sudo <slug> update     # بهروزرسانی
sudo <slug> uninstall  # حذف
```

</div>
