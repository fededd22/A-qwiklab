#!/usr/bin/env python3
"""
سكربت لتشغيل Google Cloud Shell في مختبر Qwiklabs والحفاظ على نشاطه
"""

import sys
import time
import http.cookiejar
import re
import os
import subprocess
from datetime import datetime
from urllib.parse import urlparse, parse_qs

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("❌ Playwright غير مثبت. قم بتشغيل:")
    print("   pip install playwright")
    print("   playwright install chromium")
    sys.exit(1)

# إعدادات المختبر
COOKIE_FILE = "qwiklabs_cookies.txt"  # ملف الكوكيز الخاص بمختبر Qwiklabs
PROJECT_URL = "https://console.cloud.google.com/home/dashboard?project=qwiklabs-gcp-01-346b458967df&walkthrough_id=https%3A%2F%2Fwww.skills.google%2Fdisplay_in_context%3Fdisplay_token%3DHjDhi4qmReIhZByxbOuiNiHkbUjQ15ITyLs5ItZbcWU"
SSO_URL = "https://www.skills.google/google_sso?fallback=https%3A%2F%2Faccounts.google.com%2FAddSession%3Fservice%3Daccountsettings%26sarp%3D1%26continue%3Dhttps%253A%252F%252Fconsole.cloud.google.com%252Fhome%252Fdashboard%253Fproject%253Dqwiklabs-gcp-01-346b458967df%2526walkthrough_id%253Dhttps%25253A%25252F%25252Fwww.skills.google%25252Fdisplay_in_context%253Fdisplay_token%253DHjDhi4qmReIhZByxbOuiNiHkbUjQ15ITyLs5ItZbcWU%23Email%3Dstudent-01-74cc66328b69%40qwiklabs.net&relay=https%3A%2F%2Fconsole.cloud.google.com%2Fhome%2Fdashboard%3Fproject%3Dqwiklabs-gcp-01-346b458967df%26walkthrough_id%3Dhttps%253A%252F%252Fwww.skills.google%252Fdisplay_in_context%253Fdisplay_token%253DHjDhi4qmReIhZByxbOuiNiHkbUjQ15ITyLs5ItZbcWU&token=E8UXIyvUAZ5htO6E6IS1ok1orOPcmGACR__8im79pAQ"
REFRESH_INTERVAL_SECONDS = 15  # زيادة قليلاً لتجنب الحظر
SCRIPT_NAME = "keep_qwiklabs_alive.py"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def netscape_cookie_to_playwright(cookie) -> dict:
    pw_cookie = {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "secure": bool(cookie.secure),
        "httpOnly": bool(cookie._rest.get("HttpOnly", False)) if hasattr(cookie, "_rest") else False,
    }
    if cookie.expires:
        pw_cookie["expires"] = cookie.expires
    return pw_cookie


def load_cookies_for_playwright():
    if not os.path.exists(COOKIE_FILE):
        log(f"❌ ملف {COOKIE_FILE} غير موجود")
        log("📌 قم بتصدير كوكيز جلسة Qwiklabs من Firefox أو Chrome")
        log("📌 تأكد من أن لديك كوكيز لـ: .google.com, .qwiklabs.com, .skills.google")
        return []
    
    jar = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as e:
        log(f"❌ خطأ في تحميل الكوكيز: {e}")
        return []
    
    cookies = [netscape_cookie_to_playwright(c) for c in jar]
    log(f"تم تحميل {len(cookies)} كوكي")
    return cookies


def handle_sso_login(page):
    """معالجة تسجيل الدخول عبر SSO إذا لزم الأمر"""
    log("🔍 التحقق من حالة تسجيل الدخول...")
    
    try:
        # التحقق من وجود صفحة تسجيل الدخول
        page.wait_for_timeout(2000)
        
        # البحث عن حقل البريد الإلكتروني
        email_field = page.locator("input[type='email'], input[name='identifier'], input[aria-label*='email']").first
        if email_field.count() > 0 and email_field.is_visible(timeout=2000):
            log("📧 صفحة تسجيل الدخول - جاري محاولة تسجيل الدخول...")
            
            # محاولة استخراج البريد من URL أو استخدام الافتراضي
            email = "student-01-74cc66328b69@qwiklabs.net"
            email_field.fill(email)
            page.wait_for_timeout(1000)
            
            # الضغط على زر التالي
            next_btn = page.locator("button:has-text('Next'), button:has-text('التالي')").first
            if next_btn.count() > 0:
                next_btn.click()
                page.wait_for_timeout(3000)
            
            # البحث عن حقل كلمة المرور (إذا كان مطلوباً)
            password_field = page.locator("input[type='password']").first
            if password_field.count() > 0 and password_field.is_visible(timeout=2000):
                log("🔑 مطلوب كلمة مرور - يرجى إدخالها يدوياً أو تحديث الكوكيز")
                return False
        
        # التحقق من نجاح تسجيل الدخول
        if page.url.startswith("https://console.cloud.google.com/"):
            log("✅ تم تسجيل الدخول بنجاح")
            return True
            
        # التحقق من وجود زر حساب المستخدم
        user_btn = page.locator("button[aria-label*='Account'], button[aria-label*='account'], .user-avatar").first
        if user_btn.count() > 0 and user_btn.is_visible(timeout=2000):
            log("✅ تم تسجيل الدخول (تم التحقق من زر المستخدم)")
            return True
            
        return False
        
    except Exception as e:
        log(f"⚠️ خطأ في التحقق من تسجيل الدخول: {e}")
        return False


def activate_cloud_shell(page):
    """تشغيل Cloud Shell في Google Cloud Console"""
    log("🔍 جاري البحث عن زر تفعيل Cloud Shell...")
    
    for attempt in range(6):
        page.wait_for_timeout(2000)
        
        # محددات زر تفعيل Cloud Shell في Google Cloud Console
        selectors = [
            "button:has-text('Activate Cloud Shell')",
            "button:has-text('Open Cloud Shell')",
            "button:has-text('Start Cloud Shell')",
            "button[aria-label*='Cloud Shell']",
            "button:has-text('activate')",
            "button:has-text('shell')",
            "button:has(svg[viewBox*='terminal'])",
            "button:has(svg[viewBox*='shell'])",
            "button[class*='shell']",
            "button[class*='cloud-shell']",
            "button[data-testid*='shell']",
            "button[data-testid*='cloud']",
            "button[aria-label*='shell']",
            "button[aria-label*='terminal']",
            "button:has-text('>_')",
            "button:has-text('$_')",
            "button:has-text('console')",
            "button:has-text('▶')",
            "button:has-text('▼')",
            "[role='button']:has-text('Cloud Shell')",
            "button[class*='activate']",
        ]
        
        for selector in selectors:
            try:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=1000):
                    log(f"✅ تم العثور على زر Cloud Shell: {selector}")
                    btn.click()
                    log("🟢 تم تفعيل Cloud Shell")
                    page.wait_for_timeout(5000)
                    return True
            except:
                continue
        
        # محاولة JavaScript للعثور على الزر
        try:
            result = page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button, [role="button"]');
                    for (let btn of buttons) {
                        const text = (btn.textContent || '').toLowerCase();
                        const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                        const title = (btn.getAttribute('title') || '').toLowerCase();
                        const className = (btn.className || '').toLowerCase();
                        
                        if (text.includes('activate') || text.includes('shell') || text.includes('terminal') || 
                            label.includes('activate') || label.includes('shell') || label.includes('terminal') ||
                            title.includes('activate') || title.includes('shell') || title.includes('terminal') ||
                            className.includes('activate') || className.includes('shell') || className.includes('terminal')) {
                            btn.click();
                            return 'clicked';
                        }
                    }
                    return 'not_found';
                }
            """)
            if result == 'clicked':
                log("✅ تم تفعيل Cloud Shell عن طريق JavaScript")
                page.wait_for_timeout(5000)
                return True
        except:
            pass
        
        if attempt < 5:
            log(f"⚠️ محاولة {attempt + 1}/6...")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
    
    return False


def get_shell_status(page):
    """التحقق من حالة Cloud Shell"""
    try:
        # البحث عن iframe الخاص بـ Cloud Shell
        shell_iframe = page.locator("iframe[src*='cloud-shell'], iframe[src*='shell'], iframe[title*='Cloud Shell']").first
        if shell_iframe.count() > 0 and shell_iframe.is_visible(timeout=2000):
            return "running"
        
        # البحث عن عناصر تشير إلى أن Shell يعمل
        terminal_elements = page.locator(".xterm, .terminal, div[class*='terminal'], div[class*='xterm']").first
        if terminal_elements.count() > 0 and terminal_elements.is_visible(timeout=1000):
            return "running"
        
        # البحث عن زر إيقاف Shell
        stop_btn = page.locator("button:has-text('Stop Cloud Shell'), button:has-text('Close Cloud Shell'), button[aria-label*='close shell']").first
        if stop_btn.count() > 0 and stop_btn.is_visible(timeout=1000):
            return "running"
        
        return "stopped"
    except:
        return "unknown"


def run_once():
    """تشغيل دورة واحدة"""
    log("🚀 بدء دورة جديدة لـ Google Cloud Shell في مختبر Qwiklabs")
    
    cookies = load_cookies_for_playwright()
    if not cookies:
        log("❌ لا توجد كوكيز صالحة")
        log("📌 الخطوات المطلوبة:")
        log("   1. افتح متصفح Firefox/Chrome")
        log("   2. سجل الدخول إلى مختبر Qwiklabs")
        log("   3. استخدم إضافة لتصدير الكوكيز بصيغة Netscape")
        log("   4. احفظ الملف باسم 'qwiklabs_cookies.txt'")
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.add_cookies(cookies)
        page = context.new_page()

        log(f"📂 فتح: {PROJECT_URL}")
        page.goto(PROJECT_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        # التحقق من SSO وإعادة التوجيه
        if "accounts.google.com" in page.url or "skills.google" in page.url:
            log("🔄 جاري معالجة SSO...")
            if not handle_sso_login(page):
                log("❌ فشل تسجيل الدخول - قد تحتاج إلى تحديث الكوكيز")
                browser.close()
                return False
            # إعادة التوجيه إلى المشروع
            page.goto(PROJECT_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

        # التحقق من تسجيل الدخول
        if not handle_sso_login(page):
            log("❌ غير مسجل دخول - قم بتحديث الكوكيز")
            browser.close()
            return False

        log("✅ تم تسجيل الدخول بنجاح")

        # التحقق من حالة Cloud Shell
        status = get_shell_status(page)
        log(f"📊 حالة Cloud Shell: {status}")

        # تفعيل Shell إذا كان متوقفاً
        if status == "stopped":
            if activate_cloud_shell(page):
                log("✅ تم تفعيل Cloud Shell")
            else:
                log("⚠️ فشل في تفعيل Cloud Shell - قد يكون Shell قيد التشغيل بالفعل")
        elif status == "running":
            log("✅ Cloud Shell يعمل بالفعل")
        else:
            log("⚠️ حالة غير معروفة - محاولة التفعيل...")
            activate_cloud_shell(page)

        # محاولة تنفيذ أمر بسيط للحفاظ على النشاط
        try:
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)
            page.keyboard.type("echo 'Keeping session alive'")
            page.wait_for_timeout(500)
            page.keyboard.press("Enter")
            log("🔄 تم إرسال أمر للحفاظ على النشاط")
        except:
            pass

        browser.close()

    # حفظ معلومات الجلسة
    with open("qwiklabs_session_status.txt", "w") as f:
        f.write(f"آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"الحالة: {status}\n")
        f.write(f"المشروع: qwiklabs-gcp-01-346b458967df\n")

    return True


def main():
    """الحلقة الرئيسية"""
    log("🔥 بدء التشغيل لإبقاء Google Cloud Shell في مختبر Qwiklabs نشطاً")
    log(f"⏱️ سيعاد التشغيل كل {REFRESH_INTERVAL_SECONDS} ثانية")
    log("📌 تأكد من وجود ملف 'qwiklabs_cookies.txt' مع كوكيز صالحة")
    
    while True:
        try:
            run_once()
            
            log(f"⏳ الانتظار {REFRESH_INTERVAL_SECONDS} ثواني...")
            for i in range(REFRESH_INTERVAL_SECONDS, 0, -1):
                if i % 5 == 0 or i <= 3:
                    log(f"⏳ {i}s")
                time.sleep(1)
            
            log("🔄 بدء دورة جديدة...")
            print("-" * 60)
            
        except KeyboardInterrupt:
            log("⏹️ تم الإيقاف")
            break
        except Exception as e:
            log(f"❌ خطأ: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()