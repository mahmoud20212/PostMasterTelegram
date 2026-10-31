# تشغيل OnePost على Linux VPS عبر Docker

هذه بيئة إنتاج **جديدة وفارغة** تستخدم بوت Telegram الحالي. لا تستورد أي بيانات
من PostgreSQL أو SQLite المحلية. سيعيد المستخدمون ربط حساباتهم في الإنتاج.
الترحيلات تنشئ الباقة المجانية فقط؛ أضف الباقات المدفوعة ومددها وأسعارها من الإدارة.

## المتطلبات

- Linux VPS مع Docker Engine وDocker Compose حديث (v2.24 أو أحدث)، وPython 3.
- عنوان IP عام ثابت خاص بالخادم، وإتاحة TCP 80 و443 في جدار الخادم ولوحة الاستضافة.
- عدم وجود خدمة أخرى تستخدم 80/443. لا تفتح منافذ 5432 أو 6379 أو 8000 للخارج.
- توكن البوت الحالي وTelegram API ID وAPI hash. اجعل البوت مشرفًا في `@onepost_group` للتحقق من العضوية.
- نفّذ الأوامر من مجلد المشروع في طرفية الخادم أو وحدة تحكم مزود الاستضافة.

الدخول للإدارة **من المتصفح مباشرة** عبر `https://YOUR_PUBLIC_IP/admin/`، دون نفق SSH.
يدعم الإعداد IPv4 وIPv6 العامين؛ تستخدم روابط IPv6 أقواسًا مثل `https://[IPv6]/admin/`.
إتاحة IPv6 خارجيًا تعتمد أيضًا على إعداد Docker وشبكة الخادم؛ تحقّق منها قبل الاعتماد عليه وحده.

## 1. إعداد الأسرار مرة واحدة

انقل ملفات الكود وملفات النشر إلى الخادم، دون نسخ `.env` المحلية أو قواعد البيانات أو الجلسات.
استبدل القيم التالية بعنوان الخادم والبريد الحقيقي:

```sh
python3 -m production.setup --ip YOUR_PUBLIC_IP --email YOUR_EMAIL
```

سيطلب التوكن وAPI hash دون طباعتهما، ويولّد كلمات مرور PostgreSQL وRedis ومفتاح Django
ومفتاح تشفير جلسات مستقلين. لا يتصل بـTelegram ولا يبدأ أي عملية.
يرفض إعادة الكتابة إذا كانت `.env.production` أو ملفات الأسرار موجودة.

ملف `.env.production` لا يحتوي كلمات مرور. ملفات الأسرار في `.production/secrets`؛
الدليل على الخادم بصلاحية `0700` والملفات `0444` لتقرأها هويات الخدمات المختلفة عند
تركيبها منفردة. لا توسّع صلاحية دليل الأسرار ولا تنقله إلى مجلد عام.
Compose secrets ملفات مركّبة، وليست نظام تشفير للقرص؛ احمِ الخادم والنسخ الاحتياطية.
احفظ نسخة آمنة خارج الخادم من `session_key`؛ قاعدة البيانات وحدها لا تكفي لاستعادة الجلسات.
لا تغيّر المفتاح عند تحديث الصورة.

لتقصير الأوامر في جلسة الطرفية الحالية:

```sh
dc() { docker compose --project-name onepost-production --env-file .env.production -f docker-compose.production.yml "$@"; }
dc --profile live --profile ops config --quiet
dc --profile ops build web proxy certbot
```

يمكن تعيين `APP_IMAGE` و`PROXY_IMAGE` و`CERTBOT_IMAGE` في ملف الإعداد إلى تاغات إصدار
محددة للاحتفاظ بصور يمكن الرجوع إليها. لا تغيّر `uv.lock` عند البناء؛ البناء يفشل إذا لم يطابق المشروع.

## 2. تشغيل الإدارة وقاعدة البيانات

```sh
dc up -d --wait --wait-timeout 120 db redis
dc up -d --wait --wait-timeout 180 web proxy certificate-renewal
dc ps -a
dc logs initialize
```

خدمة `initialize` تنفّذ `migrate` و`collectstatic` قبل الويب. ليست هذه العملية جزءًا
من بدء كل عامل أو بوت. إذا فشلت التهيئة، أصلح السبب قبل المتابعة؛ لا تحذف volumes لإخفاء الخطأ.
يقرأ الويب الملفات الثابتة من volume للقراءة فقط. التطبيق يعمل بالمستخدم `10001`.
سجلات الخدمات على stdout مع تدوير Docker؛ لا تتشارك العمليات ملف سجل قابلًا للتدوير.

قبل وجود الشهادة، يُرجع HTTP خطأ 503 لصفحة الإدارة ويخدم تحديات ACME فقط.
لم يبدأ البوت أو العامل أو مجدولا النشر في هذه المرحلة؛ خدماتها تحت profile اسمه `live`.

## 3. إصدار HTTPS لعنوان IP

يمكن فحص التحدي أولًا باستخدام شهادة تجريبية غير موثوقة:

```sh
dc --profile ops run --rm --no-deps certbot issue --staging
```

الإصدار التجريبي محفوظ في مسار منفصل ولا يُعرض للمتصفح ولا تستعمله تجديدات الإنتاج.
للحصول على الشهادة الموثوقة:

```sh
dc --profile ops run --rm --no-deps certbot issue
dc logs --tail 100 proxy certificate-renewal
```

بعد نجاح الإصدار، يعيد Nginx تحميل الإعداد خلال نحو 10 ثوانٍ. افتح:

```text
https://YOUR_PUBLIC_IP/admin/
```

الصفحة الرئيسية `/` توجّه إلى `/admin/`، وHTTP يحوّل إلى HTTPS دون تحويل مسار تحديات ACME.
تبقى كوكيز الدخول وCSRF آمنة. لا تُعطّل HTTPS أو تتجاوز تحذير الشهادة لتسجيل الدخول.

شهادات IP من Let's Encrypt قصيرة العمر (نحو ستة أيام). حاوية `certificate-renewal`
تفحص التجديد كل 12 ساعة؛ نجاح التجديد يكتب إشارة في volume مشترك، وNginx يعيد التحميل.
لا Docker socket داخل الحاويات. احفظ volumes الخاصة بالشهادات وحساب ACME.
افحص سجلات التجديد وصلاحية الشهادة دوريًا؛ فشل التجديد يظهر في السجل ولا ينتج إشعار Telegram.

المسار يعتمد على Certbot 5.9.0 و`webroot` و`shortlived` و`--ip-address`:
[شرح Let's Encrypt الرسمي](https://letsencrypt.org/2026/03/11/shorter-certs-certbot.html).

أنشئ حساب الإدارة تفاعليًا، ثم أضف الباقات من المتصفح:

```sh
dc run --rm --no-deps web python manage.py createsuperuser
```

ملاحظة: `docker compose exec` لا يعيد تنفيذ entrypoint، فلا يقرأ ملفات الأسرار تلقائيًا.
لأوامر الإدارة استخدم `run --rm --no-deps web` كما سبق، أو لفّ أمر `exec` هكذا:

```sh
dc exec web python -m production.entrypoint python manage.py check --deploy
```

قد يظهر تنبيه HSTS عند قيمته الافتراضية صفر؛ لا تُفعّل خيارات HTTPS على نحو يعطّل
HTTP-01. الإعداد لا يسمح بـSQLite أو `DEBUG=True` أو مضيف عام wildcard.

## 4. الانتقال للبوت الحالي

قبل هذه الخطوة، أوقف **البوت والعامل ومجدول النشر وBeat المحلية**، وتأكد من انتهاء
الإرسال الجاري. لا تشغّل نسختين من polling بالتوكن نفسه، ولا تترك نشرًا محليًا يعمل
بالتزامن مع إعادة ربط الحسابات بالإنتاج. راجع أي طلبات دفع أو فواتير Stars غير محسومة
في القاعدة القديمة قبل الانتقال إلى قاعدة فارغة.

عندما تصبح جاهزًا للانتقال الفعلي:

```sh
dc --profile live up -d --wait --wait-timeout 180 bot worker scheduler beat
dc --profile live ps
dc logs --tail 100 bot worker scheduler beat
```

احتفظ بنسخة واحدة من البوت وBeat ومجدول النشر. العامل prefork بتزامن افتراضي 2
وقنوات `publishing,notifications,celery`؛ يمكن تغيير `CELERY_CONCURRENCY` بعد القياس.
هذا ليس تقديرًا لسعة المستخدمين. مهلة إيقاف العامل 90 ثانية للسماح بإنهاء الخطوات المحدودة.
لا تستخدم transaction pooling بين التطبيق وPostgreSQL لأن أقفال التنفيذ تعتمد على جلسة الاتصال.

## 5. النسخ الاحتياطي والاستعادة والتحديث

نسخة PostgreSQL بصيغة binary آمنة، دون إعادة توجيه PowerShell التي قد تغيّر الملف:

```sh
python3 -m production.backup backup
```

تُحفظ تحت `.production/backups` بصلاحيات خاصة. انقل نسخة مشفّرة خارج الخادم، واحفظ
مفتاح الجلسات المطابق لها بصورة آمنة. جرّب الاستعادة على خادم/مشروع مستقل قبل الاعتماد عليها.

للاستعادة على **بيئة الإنتاج المقصودة فقط**: أوقف التطبيق والوكيل وخدمة الشهادات،
واترك `db` و`redis` يعملان. الأمر التالي يستبدل محتوى قاعدة `onepost` ويتطلب تأكيدًا صريحًا:

```sh
dc --profile live stop bot worker scheduler beat web proxy certificate-renewal
python3 -m production.backup restore PATH_TO_BACKUP.pgdump --confirm-database onepost
```

أعد مفتاح `session_key` المطابق للنسخة، ثم أعد التهيئة للإصدار المطابق قبل بدء التطبيق.
الاستعادة ترفض العمل عند وجود خدمات تطبيق جارية. لا تحذف ملفات Redis أو أقفال المطالبات يدويًا.
عند تحديث الكود، أوقف الخدمات السابقة أولًا، خذ نسخة احتياطية، ابنِ الصور الجديدة، ثم:

```sh
dc up -d --force-recreate initialize
dc up -d --wait --wait-timeout 180 web proxy certificate-renewal
dc --profile live up -d --wait --wait-timeout 180 bot worker scheduler beat
```

لا تجمع عمليتي تهيئة متزامنتين. راجع `dc logs initialize` قبل تشغيل الخدمات.
التراجع عن ترحيل غير متوافق يحتاج الصورة والنسخة الاحتياطية والأسرار المطابقة، وليس الصورة وحدها.
**لا تستخدم `down --volumes` في الإنتاج**. ملفات PostgreSQL وRedis وBeat والشهادات تبقى في named volumes.
سياسة `restart` تعيد تشغيل العملية التي خرجت؛ حالة `unhealthy` وحدها لا تعني أن Docker أعاد تشغيلها.

## 6. الاختبارات دون Telegram حقيقي

اختبارات الإعدادات وCompose والأسرار ونسخ البيانات، على بيئة الاختبار المعزولة:

```sh
python manage.py test production.test_configuration --settings=config.settings.testing --noinput
```

عندما يتوفر محرك Docker، الاختبار التالي ينشئ مشروعًا عشوائيًا منفصلًا باسم
`onepost-smoke-...`، بأسرار وهمية ومنافذ محلية عشوائية، ولا يقرأ أسرار الإنتاج:

```sh
python3 -m production.smoke --build
```

يختبر PostgreSQL وRedis وبقاء البيانات والجلسة المشفرة بعد إعادة إنشاء الحاويات، وصلاحيات الدور،
ومهمة Celery محدودة وأقفال التنفيذ والإدارة وCSRF والملفات الثابتة. يستعمل CA محلية مصطنعة
لاختبار HTTPS وتحويل HTTP وإعادة تحميل شهادة متجددة. تشغيل bot/polling داخل هذا الفحص مُحاكى.
لا يبدأ بوتًا حيًا، ولا يستدعي Telethon، ولا يطلب شهادة عامة فعلية.
الاختبار يحذف volumes الخاصة **بمشروع الاختبار العشوائي فقط** عند الانتهاء، ولا يزيل صور البناء.
نجاحه لا يثبت نجاح إصدار شهادة Let's Encrypt على شبكة الخادم؛ التحدي العام يحتاج اختبارًا هناك.
