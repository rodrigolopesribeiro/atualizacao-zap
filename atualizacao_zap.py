import base64
import imaplib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import traceback
import unicodedata
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import tempfile
import zipfile

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import InvalidSessionIdException, TimeoutException, WebDriverException
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()


def _arg_value(name, default=None):
    try:
        idx = sys.argv.index(name)
        return sys.argv[idx + 1]
    except (ValueError, IndexError):
        return default


# === CONFIGURACOES CRM ===
USUARIO = os.environ["CRM_USUARIO"]
SENHA = os.environ["CRM_SENHA"]
CRM_URL = "https://www.rioorla.com.br/crm/p.php"
HOJE = datetime.now().strftime("%d/%m/%Y")
TEXTO_ATUALIZACAO = f"<p>Atualizado em {HOJE}.</p>"

# === CONFIGURACAO DO PORTAL-ALVO ===
PORTAL_TARGET_ID = os.getenv("PORTAL_TARGET_ID", "").strip()
PORTAL_TARGET_NAME = os.getenv("PORTAL_TARGET_NAME", "").strip()
PORTAL_TARGET_FILE = os.getenv("PORTAL_TARGET_FILE", "").strip()
PORTAL_VERIFY_TARGET = os.getenv("PORTAL_VERIFY_TARGET", "canal_pro").strip()
VIVAREAL_VALUE = "9"  # compatibilidade com checkpoints antigos

# === CONFIGURACOES CANAL PRO ===
CANALPRO_EMAIL = os.environ["CANALPRO_EMAIL"]
CANALPRO_SENHA = os.environ["CANALPRO_SENHA"]
GMAIL_CREDENTIALS_FILE = "gmail_credentials.json"
GMAIL_TOKEN_FILE       = "gmail_token.json"
GMAIL_SCOPES           = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
GMAIL_DESTINATARIO     = "mkmarcoslopes@gmail.com"
CANAL_PRO_URL_LOGIN    = "https://canalpro.grupozap.com/login"
CANAL_PRO_URL_LISTINGS = "https://canalpro.grupozap.com/ZAP_OLX/0/listings"
VERIFICACAO_INTERVALO_SEGUNDOS = 1800  # 30 minutos entre verificações
VERIFICACAO_TIMEOUT_SEGUNDOS   = 8 * 3600  # timeout máximo de 8 horas
MINIMO_CODIGOS_ESPERADOS_CANAL_PRO = 10
MAX_ERROS_CONSECUTIVOS_SCRAPING = 5
# Aliases para compatibilidade
CANALPRO_LOGIN_URL = CANAL_PRO_URL_LOGIN
CANALPRO_LISTINGS_BASE_URL = CANAL_PRO_URL_LISTINGS
POLLING_INTERVAL_SECONDS = VERIFICACAO_INTERVALO_SEGUNDOS
MAX_WAIT_SECONDS = VERIFICACAO_TIMEOUT_SEGUNDOS
SCHEDULED_TASK_NAME = "Atualizar Imóveis"
ALVO_EXECUCAO_HORA = 23
ALVO_EXECUCAO_MINUTO = 0

CATEGORIAS_VIVAREAL = {
    "0": "Simples",
    "1": "Destaque Padrão",
    "2": "Super Destaque",
    "3": "Destaque Superior",
    "4": "Destaque Exclusivo",
    "7": "Destaque Triplo",
}

# ⚠️ APENAS PARA TESTE — voltar para False em execuções normais de produção
# Quando True: pula a Parte 1, lê imoveis_parte1.json e começa na Parte Intermediária
MODO_PULAR_PARTE_1 = False
EXPECTATIVA_MINIMA_PARTE_1 = 5  # alerta se Parte 1 processar menos que isso
MODO_HEADLESS = (
    os.getenv("MODO_HEADLESS", "false").lower() == "true"
    or os.getenv("RUN_HEADLESS", "0").lower() in ("1", "true", "yes")
)
DRY_RUN       = os.getenv("DRY_RUN",  "false").lower() == "true"
SAFE_MODE     = os.getenv("SAFE_MODE", "false").lower() == "true"

CHECKPOINT_DIR = "state"
os.makedirs(CHECKPOINT_DIR, exist_ok=True)
RUNS_DIR = os.path.join(CHECKPOINT_DIR, "runs")
ARCHIVE_DIR = os.path.join(CHECKPOINT_DIR, "archive")
IMOVEIS_PARTE1_PATH = "imoveis_parte1.json"

# === CONFIGURACOES PROXY WEBSHARE (Brasil) ===
PROXY_ATIVO  = os.getenv("PROXY_ATIVO", "false").lower() == "true"
PROXY_HOST   = "p.webshare.io"
PROXY_PORTA  = "80"
PROXY_USUARIO = os.getenv("PROXY_USUARIO", "jecuapfw-br-1")
PROXY_SENHA  = os.getenv("PROXY_SENHA", "8a7gx6ckzexa")

# Inicializados dentro de main() após decisão de espera interna
driver = None
wait = None
actions = None
HEALTHCHECK_ONLY = "--healthcheck" in sys.argv
TEST_CANAL_PRO_LOGIN_ONLY = "--test-canal-pro-login" in sys.argv
AUDIT_PORTAL_UPDATE_ONLY = "--audit-portal-update" in sys.argv
AUDIT_PROPERTY_PORTAL_ONLY = "--audit-property-portal" in sys.argv
AUDIT_CLICK_UPDATE = "--audit-click-update" in sys.argv or os.getenv("AUDIT_CLICK_UPDATE", "false").lower() == "true"
ARG_CODIGO = _arg_value("--codigo")
ARG_PORTAL_ID = _arg_value("--portal-id")


# =============================================================================
# UTILITÁRIOS GERAIS
# =============================================================================

def require_portal_target_config():
    missing = []
    if not PORTAL_TARGET_ID:
        missing.append("PORTAL_TARGET_ID")
    if not PORTAL_TARGET_NAME:
        missing.append("PORTAL_TARGET_NAME")
    if not PORTAL_TARGET_FILE:
        missing.append("PORTAL_TARGET_FILE")
    if missing:
        raise Exception(
            "PORTAL_TARGET_ID nao configurado. Defina 9 para VivaReal ou 61 para OLX Brasil. "
            f"Variaveis ausentes: {', '.join(missing)}"
        )
    return {
        "id": PORTAL_TARGET_ID,
        "name": PORTAL_TARGET_NAME,
        "file": PORTAL_TARGET_FILE,
        "verify_target": PORTAL_VERIFY_TARGET,
    }


def portal_target_label(portal_id=None, portal_name=None, portal_file=None):
    portal_id = str(portal_id or PORTAL_TARGET_ID or "?").strip()
    portal_name = (portal_name or PORTAL_TARGET_NAME or "Portal nao configurado").strip()
    portal_file = (portal_file or PORTAL_TARGET_FILE or "?").strip()
    return f"{portal_name} (id={portal_id}, arquivo={portal_file})"


def _portal_checkbox_css(portal_id=None):
    portal_id = str(portal_id or PORTAL_TARGET_ID).strip()
    return f"input[data-tipo='portaispagos'][data-portal-check='1'][value='{portal_id}']"


def _xpath_literal(value):
    value = str(value)
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


def safe_click(elem):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
    driver.execute_script("arguments[0].click();", elem)


def is_session_alive():
    try:
        _ = driver.current_url
        return True
    except Exception:
        return False


def debug_modal_state(prefix="debug"):
    try:
        print(f"🧪 DEBUG {prefix} - URL:", driver.current_url)

        active_tabs = driver.find_elements(By.CSS_SELECTOR, "li.active a, li[class*='active'] a")
        print("🧪 Abas ativas:", [t.text for t in active_tabs])

        tabs = driver.find_elements(By.XPATH, "//li/a[contains(@data-toggle,'tab') or contains(@href,'modal')]")
        print("🧪 Abas encontradas:", [t.text for t in tabs])

        html = driver.execute_script(
            """
            const modal = document.querySelector('.modal-dialog') || document.querySelector('.modal');
            return modal ? modal.innerHTML.slice(0, 4000) : document.body.innerHTML.slice(0, 4000);
            """
        )
        print("🧪 HTML modal/body parcial:")
        print(html)

    except Exception as e:
        print(f"⚠️ Falha no debug_modal_state: {type(e).__name__} | {repr(e)}")


def _fechar_todos_modais_js():
    """Fecha todos os modais via JavaScript puro — não depende de referências Selenium."""
    try:
        driver.execute_script(
            """
            document.querySelectorAll('.modal').forEach(function(m) {
                m.style.display = 'none';
                m.classList.remove('in', 'show');
            });
            document.body.classList.remove('modal-open');
            document.body.style.paddingRight = '';
            document.querySelectorAll('.modal-backdrop').forEach(function(b) { b.remove(); });
            """
        )
    except Exception:
        pass


def close_any_open_modal():
    try:
        for _ in range(3):
            modals = driver.find_elements(By.CSS_SELECTOR, ".modal-dialog, .modal-content")
            # StaleElement-safe: verifica is_displayed() individualmente
            visible_modals = []
            for m in modals:
                try:
                    if m.is_displayed():
                        visible_modals.append(m)
                except Exception:
                    pass
            if not visible_modals:
                return

            close_buttons = driver.find_elements(
                By.XPATH,
                "//button[@data-dismiss='modal']"
                " | //button[contains(@class,'close')]"
                " | //button[contains(@class,'btn-danger') and .//i[contains(@class,'fa-times')]]"
            )

            clicked = False
            for btn in close_buttons:
                try:
                    if btn.is_displayed():
                        safe_click(btn)
                        clicked = True
                        time.sleep(1)
                        break
                except Exception:
                    pass

            if not clicked:
                _fechar_todos_modais_js()
                time.sleep(1)

        print("🧹 Modais fechados/limpos.")
    except Exception as exc:
        print(f"⚠️ Falha ao fechar modais: {type(exc).__name__} | {repr(exc)}")
        _fechar_todos_modais_js()


def close_known_popup_modals():
    try:
        popup_titles = [
            "Envio de imóveis ao OLX",
            "Envio de imóveis ao OLX".lower(),
        ]
        close_buttons = driver.find_elements(
            By.XPATH,
            "//div[contains(@class,'modal-content')]//button[@data-dismiss='modal']"
        )
        for btn in close_buttons:
            try:
                modal = btn.find_element(By.XPATH, "./ancestor::div[contains(@class,'modal-content')][1]")
                title = (modal.find_element(By.XPATH, ".//h3|.//h4").text or "").strip()
                if title.lower() in popup_titles:
                    safe_click(btn)
                    time.sleep(0.8)
                    print(f"🧹 Popup fechado: {title}")
            except Exception:
                pass
    except Exception as exc:
        print(f"⚠️ Falha ao fechar popups conhecidos: {type(exc).__name__} | {repr(exc)}")


# =============================================================================
# NAVEGAÇÃO CRM
# =============================================================================

def go_to_home_screen():
    close_any_open_modal()
    driver.get(CRM_URL)
    time.sleep(3)

    try:
        usuario = driver.find_elements(By.NAME, "usuario")
        if usuario:
            wait.until(EC.visibility_of_element_located((By.NAME, "usuario"))).clear()
            driver.find_element(By.NAME, "usuario").send_keys(USUARIO)
            driver.find_element(By.NAME, "senha").clear()
            driver.find_element(By.NAME, "senha").send_keys(SENHA + Keys.RETURN)
            time.sleep(5)
            print("🔐 Login refeito.")
    except Exception as exc:
        print(f"⚠️ Verificação de login falhou: {type(exc).__name__} | {repr(exc)}")

    print("🏠 Tela inicial carregada.")


def go_to_imoveis_page_fresh():
    go_to_home_screen()
    close_any_open_modal()

    try:
        imoveis = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[contains(@onclick,'mdListImoveis') and .//span[contains(normalize-space(.),'Imóveis')]]"
                    " | //a[contains(@onclick,'mdListImoveis') and .//i[contains(@class,'fa-home')]]"
                )
            )
        )
        safe_click(imoveis)
        time.sleep(3)

        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[data-input='codigo'][data-table='imovel']")
            )
        )

        print("✅ Tela de imóveis aberta do zero.")
        return True

    except Exception as exc:
        print(f"⛔ Falha ao abrir tela de imóveis: {type(exc).__name__} | {repr(exc)}")
        debug_modal_state("erro_go_to_imoveis_page_fresh")
        return False


def clear_filters_if_possible():
    try:
        limpar = driver.find_elements(By.XPATH, "//a[contains(normalize-space(.),'Limpar filtros')]")
        for link in limpar:
            if link.is_displayed():
                safe_click(link)
                time.sleep(2)
                print("🧹 Filtros limpos.")
                return
    except Exception as exc:
        print(f"⚠️ Não consegui limpar filtros: {type(exc).__name__} | {repr(exc)}")


# =============================================================================
# EDIÇÃO DE IMÓVEL (CRM)
# =============================================================================

def update_description_text():
    editor = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.ql-editor")))
    driver.execute_script(
        """
        const el = arguments[0];
        const novo = arguments[1];
        if (!el) return;

        el.innerHTML = el.innerHTML.replace(/<p>\\s*Atualizado em .*?<\\/p>/gi, '');
        el.innerHTML = el.innerHTML.trim() + novo;

        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        """,
        editor,
        TEXTO_ATUALIZACAO,
    )


def swap_7th_with_8th_photo():
    gal = wait.until(EC.element_to_be_clickable((By.XPATH, "//li[@id='a-nav-gallery-modal']/a")))
    safe_click(gal)
    time.sleep(1.2)

    thumbs = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "#sortable .thumbnail")))
    if len(thumbs) < 8:
        print("ℹ️ Galeria com menos de 8 fotos — nada a mover.")
        return

    swapped = driver.execute_script(
        """
        const sortable = document.querySelector("#sortable");
        if (!sortable) return false;

        const thumbs = Array.from(sortable.querySelectorAll(".thumbnail"));
        if (thumbs.length < 8) return false;

        const getItem = (thumb) => thumb.closest("li") || thumb.closest(".item") || thumb.parentElement;
        const item7 = getItem(thumbs[6]);
        const item8 = getItem(thumbs[7]);
        if (!item7 || !item8) return false;

        sortable.insertBefore(item8, item7);

        sortable.dispatchEvent(new Event("change", { bubbles: true }));
        sortable.dispatchEvent(new CustomEvent("sortupdate", { bubbles: true }));
        sortable.dispatchEvent(new CustomEvent("update", { bubbles: true }));

        if (window.jQuery) {
            try {
                const $s = window.jQuery(sortable);
                $s.trigger("sortupdate");
                $s.trigger("change");
                if ($s.sortable) $s.sortable("refresh");
            } catch (e) {}
        }

        return true;
        """
    )

    if swapped:
        print("📸 Oitava foto movida para a posição da sétima.")
        return

    setima = thumbs[6]
    oitava = thumbs[7]
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", oitava)
    actions.click_and_hold(oitava).pause(0.4).move_to_element(setima).pause(0.4).release().perform()
    print("📸 Oitava foto movida para a posição da sétima (fallback drag-and-drop).")


def open_divulgacao_tab(portal_id=None):
    if portal_id is None:
        require_portal_target_config()
        portal_id = PORTAL_TARGET_ID
    try:
        tab = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//li[@id='a-nav-divulgation-modal']/a"
                    " | //a[.//i[contains(@class,'fa-bullhorn')] and contains(normalize-space(.),'Divulgação')]"
                )
            )
        )
        safe_click(tab)
        time.sleep(1.2)

        wait.until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    _portal_checkbox_css(portal_id)
                )
            )
        )

        print("📣 Aba Divulgação aberta.")
        return True

    except Exception as exc:
        print(f"⛔ Falha ao abrir aba Divulgação: {type(exc).__name__} | {repr(exc)}")
        debug_modal_state("erro_open_divulgacao")
        return False


def open_gerais_tab():
    try:
        tab = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//li[contains(@class,'active')]/a[contains(normalize-space(.),'Gerais')]"
                    " | //li[@id='a-nav-general-modal']/a"
                    " | //a[.//i[contains(@class,'fa-home')] and contains(normalize-space(.),'Gerais')]"
                )
            )
        )
        safe_click(tab)
        time.sleep(0.8)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#codigo-input")))
        print("🏠 Aba Gerais aberta.")
        return True

    except Exception as exc:
        print(f"⛔ Falha ao abrir aba Gerais: {type(exc).__name__} | {repr(exc)}")
        debug_modal_state("erro_open_gerais")
        return False


def get_target_portal_checkbox_parts(portal_id=None):
    if portal_id is None:
        require_portal_target_config()
        portal_id = PORTAL_TARGET_ID
    input_el = wait.until(
        EC.presence_of_element_located(
            (
                By.CSS_SELECTOR,
                _portal_checkbox_css(portal_id)
            )
        )
    )

    wrapper = input_el.find_element(
        By.XPATH,
        "./ancestor::div[contains(@class,'icheckbox_square-blue')]"
    )

    try:
        helper = wrapper.find_element(By.CSS_SELECTOR, "ins.iCheck-helper")
    except Exception:
        helper = wrapper

    return input_el, wrapper, helper


def is_target_portal_checked(portal_id=None):
    _, wrapper, _ = get_target_portal_checkbox_parts(portal_id)
    wrapper_class = wrapper.get_attribute("class") or ""
    return "checked" in wrapper_class


def set_target_portal_checked(checked, portal_id=None, portal_name=None):
    if portal_id is None:
        require_portal_target_config()
        portal_id = PORTAL_TARGET_ID
    portal_name = portal_name or PORTAL_TARGET_NAME or f"portal {portal_id}"
    _, wrapper, helper = get_target_portal_checkbox_parts(portal_id)
    atual = "checked" in ((wrapper.get_attribute("class") or ""))
    if atual == checked:
        print(f"INFO: {portal_name} ja esta {'marcado' if checked else 'desmarcado'}.")
        return
    safe_click(helper)
    time.sleep(0.8)
    _, wrapper, helper = get_target_portal_checkbox_parts(portal_id)
    novo = "checked" in ((wrapper.get_attribute("class") or ""))
    if novo != checked:
        driver.execute_script("arguments[0].click();", helper)
        time.sleep(0.8)
    _, wrapper, _ = get_target_portal_checkbox_parts(portal_id)
    final = "checked" in ((wrapper.get_attribute("class") or ""))
    if final != checked:
        raise Exception(f"Falha ao alterar {portal_name} para checked={checked}")
    print(f"{'OK' if checked else 'REMOVIDO'} {portal_name} {'marcado' if checked else 'desmarcado'}.")


def get_vivareal_checkbox_parts():
    return get_target_portal_checkbox_parts(VIVAREAL_VALUE)


def is_vivareal_checked():
    return is_target_portal_checked(VIVAREAL_VALUE)


def set_vivareal_checked(checked):
    return set_target_portal_checked(checked, VIVAREAL_VALUE, "VivaReal")


def get_vivareal_category_label(value):
    return CATEGORIAS_VIVAREAL.get(str(value), "Simples")


def get_target_portal_category_value(portal_id=None, portal_name=None):
    if portal_id is None:
        require_portal_target_config()
        portal_id = PORTAL_TARGET_ID
    portal_name = portal_name or PORTAL_TARGET_NAME or f"portal {portal_id}"
    selector = f"#destaque{portal_id}"
    try:
        select = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
        value = driver.execute_script("return arguments[0].value;", select) or "0"
        label = driver.execute_script(
            """
            const sel = arguments[0];
            const opt = Array.from(sel.options).find(o => o.value === sel.value);
            return opt ? opt.textContent.trim() : '';
            """,
            select,
        ) or get_vivareal_category_label(value)
        print(f"Categoria {portal_name} original: {label} ({value})")
        return value, label
    except Exception as exc:
        print(f"Aviso: nao consegui capturar categoria de {portal_name}. Usando Simples (0). Erro: {type(exc).__name__} | {repr(exc)}")
        debug_modal_state("erro_get_categoria_portal")
        return "0", "Simples"


def set_target_portal_category_value(value, portal_id=None, portal_name=None):
    if portal_id is None:
        require_portal_target_config()
        portal_id = PORTAL_TARGET_ID
    normalized = str(value) if str(value) in CATEGORIAS_VIVAREAL else "0"
    selector = f"#destaque{portal_id}"
    try:
        select = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
    except Exception:
        return
    driver.execute_script(
        """
        const el = arguments[0];
        const value = arguments[1];
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        """,
        select,
        normalized,
    )


def get_vivareal_category_value():
    return get_target_portal_category_value(VIVAREAL_VALUE, "VivaReal")


def set_vivareal_category_value(value):
    return set_target_portal_category_value(value, VIVAREAL_VALUE, "VivaReal")


def get_property_code_from_modal():
    if not open_gerais_tab():
        return ""

    campo = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#codigo-input")))
    codigo = (campo.get_attribute("value") or "").strip()

    if not codigo:
        codigo = (driver.execute_script("return arguments[0].value;", campo) or "").strip()

    print(f"🏷️ Código do imóvel capturado: {codigo}")
    return codigo


def save_property():
    try:
        save_btn = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[@id='oneClick' and .//span[normalize-space(.)='Salvar']]"
                    " | //button[contains(@class,'btn-success') and .//span[normalize-space(.)='Salvar']]"
                    " | //button[contains(@onclick,'actionSave') and contains(normalize-space(.),'Salvar')]"
                )
            )
        )
        safe_click(save_btn)

        wait.until(EC.invisibility_of_element_located((By.CLASS_NAME, "modal-dialog")))
        time.sleep(1.5)
        return True

    except Exception as exc:
        print(f"⛔ Falha ao salvar imóvel: {type(exc).__name__} | {repr(exc)}")
        debug_modal_state("erro_save_property")
        raise


def expand_menu_if_needed():
    try:
        btn_seta = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[contains(@onclick,'toggleMenu') or @onclick='toggleMenu()']")
            )
        )
        safe_click(btn_seta)
        time.sleep(1)
        print("✅ Menu lateral: toggle (seta) clicado.")
    except Exception:
        print("ℹ️ Não encontrei o botão da seta (ou não foi necessário). Seguindo...")


def apply_initial_filters():
    portal = require_portal_target_config()
    # Aguarda a página estabilizar
    time.sleep(2)

    # Encontra o link "Divulgação em Portais" via JavaScript (robusto com acentos)
    found = driver.execute_script(
        """
        const terms = ['divulga', 'portai'];
        const links = Array.from(document.querySelectorAll('a'));
        const link = links.find(a => {
            const t = (a.textContent || '').toLowerCase()
                        .normalize('NFD').replace(/[̀-ͯ]/g, '');
            return terms.every(term => t.includes(term));
        });
        if (link) { link.click(); return true; }
        return false;
        """
    )

    if not found:
        # Fallback: itera links com proteção contra StaleElement
        for _ in range(3):
            try:
                links = driver.find_elements(By.TAG_NAME, "a")
                for link in links:
                    try:
                        txt = (link.text or "").strip().lower()
                        if "divulga" in txt and "portai" in txt:
                            safe_click(link)
                            found = True
                            break
                    except Exception:
                        continue
                if found:
                    break
                time.sleep(1)
            except Exception:
                time.sleep(1)

    if not found:
        raise Exception("Não encontrei o link 'Divulgação em Portais' na tela de filtros.")

    time.sleep(1)

    wait.until(EC.element_to_be_clickable((By.XPATH, "//select[@data-input='idportal']"))).click()
    option_xpath = f"//select[@data-input='idportal']/option[@value={_xpath_literal(portal['id'])}]"
    wait.until(EC.element_to_be_clickable((By.XPATH, option_xpath))).click()
    print(f"✅ Filtro 'Divulgação em Portais - {portal['name']}' aplicado.")
    time.sleep(1)

    cap_tab = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//a[@role='button' and contains(normalize-space(.),'Captação')]"))
    )
    safe_click(cap_tab)
    time.sleep(2)

    capt_btn = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//select[@id='captador']/following-sibling::div[contains(@class,'ms-parent')]/button")
        )
    )
    safe_click(capt_btn)

    ms_drop = wait.until(
        EC.visibility_of_element_located(
            (
                By.XPATH,
                "//select[@id='captador']/following-sibling::div[contains(@class,'ms-parent')]"
                "//div[contains(@class,'ms-drop') and contains(@style,'display: block')]",
            )
        )
    )
    time.sleep(0.5)

    checkboxes = ms_drop.find_elements(By.XPATH, ".//input[@name='selectItem' and @type='checkbox']")
    for chk in checkboxes:
        label = (chk.get_attribute("data-label") or "").strip()
        if label != "Rodrigo Lopes" and chk.is_selected():
            driver.execute_script("arguments[0].click();", chk)

    rodrigo_chk = ms_drop.find_element(
        By.XPATH, ".//input[@name='selectItem' and @value='4' and @type='checkbox']"
    )
    if not rodrigo_chk.is_selected():
        driver.execute_script("arguments[0].click();", rodrigo_chk)

    print("✔️ Captado por: Rodrigo Lopes selecionado.")
    time.sleep(1)

    btn_buscar = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(text())='Buscar']")))
    safe_click(btn_buscar)
    time.sleep(5)
    print("✅ Imóveis filtrados exibidos.")


def search_property_by_code_strict(codigo, max_attempts=3):
    codigo = str(codigo).strip()
    for attempt in range(1, max_attempts + 1):
        print(f"🔎 Buscando imóvel código {codigo} | tentativa {attempt}/{max_attempts}")

        if not go_to_imoveis_page_fresh():
            continue

        close_known_popup_modals()
        clear_filters_if_possible()

        try:
            campo = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "input[data-input='codigo'][data-table='imovel']")
                )
            )

            driver.execute_script(
                """
                const el = arguments[0];
                el.focus();
                el.value = '';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                campo,
            )
            time.sleep(0.5)
            campo.send_keys(codigo)
            time.sleep(0.8)

            try:
                busca_exata = driver.find_element(By.CSS_SELECTOR, "#buscaExata")
                wrapper = busca_exata.find_element(
                    By.XPATH,
                    "./ancestor::div[contains(@class,'icheckbox_square-blue')]"
                )
                if "checked" not in (wrapper.get_attribute("class") or ""):
                    helper = wrapper.find_element(By.CSS_SELECTOR, "ins.iCheck-helper")
                    safe_click(helper)
                    time.sleep(0.5)
            except Exception as exc:
                print(f"⚠️ Não consegui validar Busca Exata: {type(exc).__name__} | {repr(exc)}")

            btn_buscar = wait.until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//button[.//i[contains(@class,'fa-search')] and contains(normalize-space(.),'Buscar')]"
                    )
                )
            )
            safe_click(btn_buscar)
            time.sleep(5)

            possible_xpaths = [
                f"//*[contains(normalize-space(.), '# {codigo}')]",
                f"//*[contains(normalize-space(.), '#{codigo}')]",
                f"//*[contains(normalize-space(.), '{codigo}')]",
            ]

            for xp in possible_xpaths:
                try:
                    driver.find_element(By.XPATH, xp)
                    print(f"✅ Resultado confirmado por texto para código {codigo}.")
                    return True
                except Exception:
                    pass

            edit_buttons = driver.find_elements(By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")
            if edit_buttons:
                print(f"⚠️ Texto do código {codigo} não confirmado, mas existe botão editar. Vou validar dentro do modal.")
                return True

            print(f"⚠️ Código {codigo} não apareceu na tentativa {attempt}. Repetindo busca...")
            debug_modal_state(f"busca_codigo_{codigo}_tentativa_{attempt}")

        except Exception as exc:
            print(f"⚠️ Falha na busca do código {codigo}: {type(exc).__name__} | {repr(exc)}")
            debug_modal_state(f"erro_busca_codigo_{codigo}_tentativa_{attempt}")

    print(f"⛔ Não consegui buscar o código {codigo} após {max_attempts} tentativas.")
    return False


def edit_property_result_by_code(codigo):
    try:
        row = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    f"//*[contains(normalize-space(.), '# {codigo}') or contains(normalize-space(.), '#{codigo}')]/ancestor::*[contains(@class,'row') or contains(@class,'item') or contains(@class,'property') or self::tr][1]"
                )
            )
        )

        edit_btn = row.find_element(By.XPATH, ".//button[contains(@onclick,'mdImovelUpdate') or .//i[contains(@class,'fa-pencil')]]")
        safe_click(edit_btn)

    except Exception:
        print(f"⚠️ Não achei linha exata do código {codigo}. Tentando fallback com primeiro botão editar após confirmação.")
        buttons = wait.until(
            EC.presence_of_all_elements_located((By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]"))
        )
        if not buttons:
            raise Exception(f"Nenhum botão editar encontrado para código {codigo}")
        safe_click(buttons[0])

    wait.until(EC.visibility_of_element_located((By.ID, "titulo-input")))
    time.sleep(1)

    codigo_modal = get_property_code_from_modal()
    if str(codigo_modal).strip().upper() != str(codigo).strip().upper():
        close_any_open_modal()
        raise Exception(f"Imóvel errado aberto. Esperado {codigo}, abriu {codigo_modal}")

    print(f"✏️ Modal correto aberto para código {codigo}.")


def _normalize_portal_id(value):
    return str(value or "").strip().strip('"').strip("'")


def _parse_update_portais_from_onclick(onclick):
    onclick = onclick or ""
    match = re.search(r"updatePortais\((.*)\)", onclick)
    if not match:
        return []
    payload = match.group(1).strip().rstrip(";")
    try:
        data = json.loads(payload)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"descricao": str(i.get("descricao", "")), "arquivo": str(i.get("arquivo", "")), "id": _normalize_portal_id(i.get("id", ""))} for i in data if isinstance(i, dict)]


def find_target_portal_update_button(portal_id=None, portal_name=None, portal_file=None):
    if portal_id is None:
        portal = require_portal_target_config()
        portal_id, portal_name, portal_file = portal["id"], portal["name"], portal["file"]
    portal_id = _normalize_portal_id(portal_id)
    candidates = driver.find_elements(By.XPATH, "//a[contains(@onclick,'updatePortais') or contains(@class,'btn-update-portal')] | //button[contains(@onclick,'updatePortais')]")
    for el in candidates:
        for item in _parse_update_portais_from_onclick(_safe_attr(el, "onclick")):
            if _normalize_portal_id(item.get("id")) == portal_id:
                return el
    raise Exception(f"Botao updatePortais nao encontrado para {portal_target_label(portal_id, portal_name, portal_file)}")


def go_to_integracoes_parceiros_and_update_target_portal(portal_id=None, portal_name=None, portal_file=None):
    if portal_id is None:
        portal = require_portal_target_config()
        portal_id, portal_name, portal_file = portal["id"], portal["name"], portal["file"]
    label = portal_target_label(portal_id, portal_name, portal_file)
    expand_menu_if_needed()
    try:
        a_integracoes = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[.//i[contains(@class,'fa-plug')] and contains(normalize-space(.),'Integra')]")))
        safe_click(a_integracoes)
        time.sleep(1)
    except Exception:
        driver.get("https://www.rioorla.com.br/crm/po.php")
        time.sleep(2)
    try:
        a_parceiros = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(@href,'po.php') and .//i[contains(@class,'fa-handshake')] and contains(normalize-space(.),'Parceiros')]")))
        safe_click(a_parceiros)
        time.sleep(2)
    except Exception:
        driver.get("https://www.rioorla.com.br/crm/po.php")
        time.sleep(2)
    btn = wait.until(lambda _driver: find_target_portal_update_button(portal_id, portal_name, portal_file))
    print(f"Atualizando portal alvo: {label}")
    print(f"Botao encontrado: {_portal_button_summary(btn)}")
    safe_click(btn)
    print(f"Atualizacao do {label} disparada.")
    time.sleep(5)


def go_to_integracoes_parceiros_and_update_vivareal():
    return go_to_integracoes_parceiros_and_update_target_portal(VIVAREAL_VALUE, "VivaReal", "vivareal.php")


def _portal_button_summary(el):
    data_attrs = {}
    try:
        data_attrs = driver.execute_script(
            """
            const out = {};
            for (const attr of arguments[0].attributes || []) {
                if (attr.name.startsWith('data-')) out[attr.name] = attr.value;
            }
            return out;
            """,
            el,
        ) or {}
    except Exception:
        pass

    summary = _element_summary(el)
    summary.update({
        "class": _safe_attr(el, "class"),
        "onclick": _safe_attr(el, "onclick"),
        "data": data_attrs,
    })
    return summary


def _collect_browser_network_logs():
    entries = []
    try:
        for item in driver.get_log("performance"):
            try:
                msg = json.loads(item.get("message", "{}")).get("message", {})
                method = msg.get("method")
                params = msg.get("params", {})
                if not method or not method.startswith("Network."):
                    continue
                request = params.get("request", {})
                response = params.get("response", {})
                url = request.get("url") or response.get("url") or ""
                if not any(term in url.lower() for term in ["rioorla", "portal", "zap", "olx", "vivareal", "po.php", "ajax"]):
                    continue
                entries.append({
                    "method": method,
                    "url": url,
                    "request_method": request.get("method"),
                    "status": response.get("status"),
                    "mimeType": response.get("mimeType"),
                    "timestamp": params.get("timestamp"),
                })
            except Exception:
                continue
    except Exception as exc:
        entries.append({"error": f"{type(exc).__name__}: {exc}"})
    return entries


def _write_audit_json(out_dir, name, data):
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _portal_input_summary(el):
    summary = _portal_button_summary(el)
    try:
        summary["raw_value"] = _safe_attr(el, "value")
        summary["checked_js"] = bool(driver.execute_script("return !!arguments[0].checked;", el))
        summary["nearby_text"] = driver.execute_script(
            """
            const el = arguments[0];
            const parent = el.closest('label, .form-group, .row, .col-md-12, li, div') || el.parentElement;
            return parent ? (parent.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 500) : '';
            """,
            el,
        ) or ""
    except Exception as exc:
        summary["portal_input_error"] = f"{type(exc).__name__}: {exc}"
    return summary


def open_divulgacao_tab_any():
    try:
        tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//li[@id='a-nav-divulgation-modal']/a | //a[.//i[contains(@class,'fa-bullhorn')] and contains(normalize-space(.),'Divulgação')]")))
        safe_click(tab)
        time.sleep(1.2)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-tipo='portaispagos'][data-portal-check='1']")))
        return True
    except Exception as exc:
        print(f"ERRO: falha ao abrir Divulgacao para auditoria: {type(exc).__name__} | {repr(exc)}")
        return False


def _audit_property_portal(codigo=None):
    require_portal_target_config()
    codigo = str(codigo or "").strip()
    if not codigo:
        raise Exception("Use --audit-property-portal --codigo CODIGO.")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("debug", f"{ts}_audit_property_portal_{codigo}")
    os.makedirs(out_dir, exist_ok=True)
    report = {"timestamp": datetime.now().isoformat(), "codigo": codigo, "portal_target": require_portal_target_config(), "safe_mode": True}
    try:
        if not search_property_by_code_strict(codigo):
            raise Exception(f"Nao consegui localizar o imovel {codigo}.")
        edit_property_result_by_code(codigo)
        if not open_divulgacao_tab_any():
            raise Exception("Nao consegui abrir Divulgacao.")
        inputs = driver.find_elements(By.CSS_SELECTOR, "input[data-tipo='portaispagos'][data-portal-check='1']")
        report["portal_inputs"] = [_portal_input_summary(el) for el in inputs]
        report["target_input"] = [i for i in report["portal_inputs"] if str(i.get("raw_value")) == str(PORTAL_TARGET_ID)]
        report["target_exists"] = bool(report["target_input"])
        report["target_checked"] = bool(report["target_input"] and report["target_input"][0].get("checked_js"))
        report["snapshot"] = save_debug_snapshot(driver, f"audit_property_portal_{codigo}")
        _write_audit_json(out_dir, "audit_property_portal.json", report)
        print(f"Auditoria do imovel salva em: {out_dir}")
        return out_dir
    finally:
        close_any_open_modal()
        close_known_popup_modals()


def _audit_portal_update():
    require_portal_target_config()
    audit_portal_id = _normalize_portal_id(ARG_PORTAL_ID or PORTAL_TARGET_ID)
    print(f"AUDITORIA PORTAL: modo seguro. Portal={portal_target_label(audit_portal_id, PORTAL_TARGET_NAME, PORTAL_TARGET_FILE)}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("debug", f"{ts}_audit_portal_update")
    os.makedirs(out_dir, exist_ok=True)
    report = {"timestamp": datetime.now().isoformat(), "safe_mode": not AUDIT_CLICK_UPDATE, "portal_target": require_portal_target_config(), "audit_portal_id": audit_portal_id}
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception as exc:
        report["network_enable_error"] = f"{type(exc).__name__}: {exc}"
    try:
        driver.get("https://www.rioorla.com.br/crm/po.php")
        time.sleep(3)
        report["partners_snapshot"] = save_debug_snapshot(driver, "audit_portal_partners")
        buttons = driver.find_elements(By.XPATH, "//a[contains(@onclick,'updatePortais') or contains(@class,'btn-update-portal')] | //button[contains(@onclick,'updatePortais')]")
        report["update_portais_buttons"] = []
        for el in buttons:
            summary = _portal_button_summary(el)
            summary["updatePortais"] = _parse_update_portais_from_onclick(summary.get("onclick") or "")
            report["update_portais_buttons"].append(summary)
        report["target_update_candidates"] = [b for b in report["update_portais_buttons"] if any(_normalize_portal_id(p.get("id")) == audit_portal_id for p in b.get("updatePortais", []))]
        if AUDIT_CLICK_UPDATE:
            btn = find_target_portal_update_button(audit_portal_id, PORTAL_TARGET_NAME, PORTAL_TARGET_FILE)
            report["clicked_button"] = _portal_button_summary(btn)
            safe_click(btn)
            time.sleep(120)
            report["network_after_click"] = _collect_browser_network_logs()
        else:
            report["click_skipped_reason"] = "Modo seguro: use --audit-click-update --portal-id ID para clicar."
    except Exception as exc:
        report["partners_audit_error"] = f"{type(exc).__name__}: {repr(exc)}"
        debug_modal_state("audit_portal_partners_error")
    report["network_logs_collected"] = _collect_browser_network_logs()
    _write_audit_json(out_dir, "audit_report.json", report)
    print(f"Auditoria salva em: {out_dir}")
    return out_dir


# =============================================================================
# CHECKPOINT / ROLLBACK
# =============================================================================

_CHECKPOINT_PATH = None   # preenchido em _checkpoint_criar()
_RUN_CONTEXT = {
    "run_id": None,
    "mode": "unknown",
    "run_state_path": None,
}


def _state_archive_file(path, motivo):
    if not path or not os.path.exists(path):
        return None
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(path)
    destino = os.path.join(ARCHIVE_DIR, f"{base}.{motivo}.{ts}")
    try:
        shutil.move(path, destino)
        print(f"🗃️ Estado antigo arquivado: {path} -> {destino}")
        return destino
    except Exception as exc:
        print(f"⚠️ Falha ao arquivar estado antigo ({path}): {exc}")
        return None


def _criar_run_context(teste_local, em_nuvem):
    origem = "teste" if teste_local else ("agendado" if em_nuvem else "manual")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{origem}"
    os.makedirs(RUNS_DIR, exist_ok=True)
    run_state_path = os.path.join(RUNS_DIR, f"run_{run_id}.json")
    _RUN_CONTEXT["run_id"] = run_id
    _RUN_CONTEXT["mode"] = origem
    _RUN_CONTEXT["run_state_path"] = run_state_path
    print(f"🧾 run_id da execução: {run_id} | modo={origem}")
    return run_id


def _run_state_salvar(status, imoveis=None):
    run_id = _RUN_CONTEXT.get("run_id")
    run_state_path = _RUN_CONTEXT.get("run_state_path")
    if not run_id or not run_state_path:
        return
    payload = {
        "run_id": run_id,
        "mode": _RUN_CONTEXT.get("mode"),
        "status": status,
        "updated_at": datetime.now().isoformat(),
        "imoveis": imoveis or [],
    }
    with open(run_state_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _preparar_estado_inicio_execucao(modo_resume=False, teste_local=False):
    """
    Em execução normal, evita reutilização indevida de estado antigo.
    Em modo resume (MODO_PULAR_PARTE_1), preserva o arquivo atual.
    """
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    os.makedirs(RUNS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    if modo_resume:
        print("♻️ Execução em modo resume: estado atual será preservado para retomada.")
        return

    if teste_local:
        print("🧪 Execução de teste/manual: não vou alterar o arquivo de estado oficial da agenda.")
        return

    if os.path.exists(IMOVEIS_PARTE1_PATH):
        try:
            with open(IMOVEIS_PARTE1_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            status_antigo = (data.get("status") or "").upper()
            run_id_antigo = data.get("run_id")
            if status_antigo == "SUCCESS":
                _state_archive_file(IMOVEIS_PARTE1_PATH, "execucao_concluida")
            else:
                _state_archive_file(IMOVEIS_PARTE1_PATH, "estado_antigo_ignorado")
            print(f"ℹ️ Estado anterior ignorado para nova execução limpa (run_id antigo={run_id_antigo}).")
        except Exception:
            _state_archive_file(IMOVEIS_PARTE1_PATH, "json_invalido")

    for file_name in os.listdir(RUNS_DIR):
        p = os.path.join(RUNS_DIR, file_name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if (data.get("status") or "").upper() == "SUCCESS":
                _state_archive_file(p, "run_concluido")
        except Exception:
            _state_archive_file(p, "run_json_invalido")

def _checkpoint_criar(timestamp_str):
    """Cria o arquivo de checkpoint no início da Parte 1."""
    global _CHECKPOINT_PATH
    fname = f"checkpoint_{timestamp_str}.json"
    _CHECKPOINT_PATH = os.path.join(CHECKPOINT_DIR, fname)
    data = {
        "timestamp": timestamp_str,
        "run_id": _RUN_CONTEXT.get("run_id"),
        "mode": _RUN_CONTEXT.get("mode"),
        "status": "IN_PROGRESS",
        "desmarcados": [],
    }
    with open(_CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"📋 Checkpoint criado: {_CHECKPOINT_PATH}")
    return _CHECKPOINT_PATH


def _checkpoint_registrar_desmarcado(codigo, categoria_value, categoria_nome):
    """Registra imediatamente cada imóvel desmarcado no checkpoint."""
    if not _CHECKPOINT_PATH or not os.path.exists(_CHECKPOINT_PATH):
        return
    try:
        with open(_CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["desmarcados"].append({
            "codigo": codigo,
            "portal_id": PORTAL_TARGET_ID,
            "portal_nome": PORTAL_TARGET_NAME,
            "portal_arquivo": PORTAL_TARGET_FILE,
            "categoria_portal": categoria_value,
            "categoria_vivareal": categoria_value,
            "categoria_nome": categoria_nome,
        })
        with open(_CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"⚠️ Falha ao atualizar checkpoint: {exc}")


def _checkpoint_fechar(status):
    """Marca o checkpoint com o status final (SUCCESS, ERROR, etc.)."""
    if not _CHECKPOINT_PATH or not os.path.exists(_CHECKPOINT_PATH):
        return
    try:
        with open(_CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["status"] = status
        data["fechado_em"] = datetime.now().isoformat()
        with open(_CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _checkpoint_carregar_desmarcados():
    """Retorna lista de imóveis desmarcados do checkpoint atual, ou [] se não houver."""
    if not _CHECKPOINT_PATH or not os.path.exists(_CHECKPOINT_PATH):
        return []
    try:
        with open(_CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("desmarcados", [])
    except Exception:
        return []


def _rollback_automatico(imoveis_para_reverter):
    """
    Tenta remarcar VivaReal para cada imóvel da lista.
    Retorna (revertidos, pendentes).
    """
    if not imoveis_para_reverter:
        return [], []

    print(f"\n🔄 ROLLBACK AUTOMÁTICO: tentando restaurar {len(imoveis_para_reverter)} imóvel(is)...")
    revertidos = []
    pendentes  = []

    for item in imoveis_para_reverter:
        codigo = (item.get("codigo") or "").strip()
        if not codigo:
            continue
        try:
            ok = _process_single_item_parte2(item)
            if ok:
                revertidos.append(item)
                print(f"   ✅ Revertido: {codigo}")
            else:
                pendentes.append(item)
                print(f"   ⚠️ Falhou: {codigo}")
        except Exception as exc:
            pendentes.append(item)
            print(f"   ⚠️ Exceção ao reverter {codigo}: {exc}")

    return revertidos, pendentes


def _gerar_arquivo_rollback_pendente(pendentes, timestamp_str):
    """Grava arquivo de pendências manuais se o rollback falhar parcialmente."""
    if not pendentes:
        return None
    fname = os.path.join(CHECKPOINT_DIR, f"rollback_pendente_{timestamp_str}.json")
    data = {
        "gerado_em": datetime.now().isoformat(),
        "instrucao": f"Remarcar manualmente o {portal_target_label()} para os imoveis abaixo no CRM.",
        "pendentes": pendentes,
    }
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return fname


# =============================================================================
# PARTE 1 — DESMARCAR VIVAREAL
# =============================================================================

def process_part_1_collect_and_disable_vivareal():
    imoveis_processados = []
    codigos_ja_processados = set()
    MAX_TENTATIVAS_PARTE_1 = 50

    pagina = 1
    while True:
        try:
            wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")))
        except TimeoutException:
            print(f"✅ Página {pagina} sem imóveis para processar.")

        # Diagnóstico antes de iniciar o loop da página
        if pagina == 1:
            print("\n🔍 DIAGNÓSTICO PRÉ-PARTE 1")
            try:
                contadores = driver.find_elements(By.XPATH, "//*[contains(text(),'registros encontrados')]")
                if contadores:
                    print(f"   📊 Filtro: {contadores[0].text.strip()}")
            except Exception:
                pass
            btns_pre = driver.find_elements(By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")
            print(f"   📊 Botões editar visíveis: {len(btns_pre)}")
            print(f"   🌐 URL: {driver.current_url}")

        tentativas_pagina = 0
        while True:
            tentativas_pagina += 1
            if tentativas_pagina > MAX_TENTATIVAS_PARTE_1:
                print(f"⛔ Limite de {MAX_TENTATIVAS_PARTE_1} tentativas atingido na página {pagina}. Encerrando.")
                break

            # Antes de contar botões, garantir que não há modal bloqueando
            _fechar_todos_modais_js()

            buttons = driver.find_elements(By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")
            if not buttons:
                # Tenta fechar modal residual e re-verifica uma vez antes de desistir
                close_any_open_modal()
                time.sleep(1.5)
                _fechar_todos_modais_js()
                time.sleep(0.5)
                buttons = driver.find_elements(By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")
                if not buttons:
                    print("✅ Nenhum imóvel restante na lista filtrada desta página.")
                    break
                print(f"ℹ️ Modal estava bloqueando lista. Continuando com {len(buttons)} botão(ões).")
                continue

            print(f"📌 Imóveis restantes na lista filtrada: {len(buttons)} | 🔢 Tentativa {tentativas_pagina}/{MAX_TENTATIVAS_PARTE_1}")

            if not is_session_alive():
                raise InvalidSessionIdException("Sessão do navegador foi encerrada.")

            try:
                safe_click(buttons[0])
                wait.until(EC.visibility_of_element_located((By.ID, "titulo-input")))

                # Captura código PRIMEIRO para verificar duplicata antes de processar
                codigo = get_property_code_from_modal()
                if not codigo:
                    raise Exception("Código do imóvel vazio")

                if codigo in codigos_ja_processados:
                    print(f"⚠️ Código {codigo} já processado nesta execução. Fechando modal e pulando.")
                    print(f"📊 Códigos já processados: {sorted(codigos_ja_processados)}")
                    close_any_open_modal()
                    close_known_popup_modals()
                    time.sleep(2)
                    continue

                update_description_text()
                swap_7th_with_8th_photo()

                if not open_divulgacao_tab():
                    raise Exception("Não abriu Divulgação")

                categoria_value, categoria_nome = get_target_portal_category_value()

                # Skip se o portal alvo ja esta desmarcado - nao reprocessar
                if not is_target_portal_checked():
                    print(f"INFO: Imovel {codigo} ja esta desmarcado em {portal_target_label()}. Pulando sem salvar.")
                    codigos_ja_processados.add(codigo)
                    close_any_open_modal()
                    close_known_popup_modals()
                    time.sleep(1.5)
                    continue

                set_target_portal_checked(False)

                if codigo not in {i["codigo"] for i in imoveis_processados}:
                    imoveis_processados.append({
                        "codigo": codigo,
                        "portal_id": PORTAL_TARGET_ID,
                        "portal_nome": PORTAL_TARGET_NAME,
                        "portal_arquivo": PORTAL_TARGET_FILE,
                        "categoria_portal": categoria_value,
                        "categoria_vivareal": categoria_value,
                        "categoria_nome": categoria_nome,
                    })

                save_property()
                _checkpoint_registrar_desmarcado(codigo, categoria_value, categoria_nome)
                print("💾 Imóvel salvo na Parte 1.")

                # Fechar modal OLX que aparece após salvar
                time.sleep(1)
                close_known_popup_modals()
                close_any_open_modal()
                time.sleep(1.5)

                codigos_ja_processados.add(codigo)

            except Exception as exc:
                print(f"⚠️ Erro na Parte 1: {type(exc).__name__} | {repr(exc)}")
                debug_modal_state("erro_parte1")
                close_any_open_modal()
                close_known_popup_modals()
                time.sleep(1)

                if isinstance(exc, (InvalidSessionIdException, WebDriverException)) and not is_session_alive():
                    raise

        try:
            next_li = driver.find_element(
                By.XPATH, "//ul[@class='pagination']/li[a/i[contains(@class,'fa-angle-right')]]"
            )
            if "disabled" in (next_li.get_attribute("class") or ""):
                print("⛔ Última página alcançada.")
                break

            safe_click(next_li.find_element(By.TAG_NAME, "a"))
            pagina += 1
            time.sleep(3.5)
        except Exception:
            print("⛔ Não foi possível avançar — encerrando loop de paginação.")
            break

    return imoveis_processados


# =============================================================================
# PARTE INTERMEDIÁRIA — VERIFICAÇÃO NO CANAL PRO (ZAP IMÓVEIS)
# =============================================================================

def _gmail_autenticar():
    """
    Autentica na Gmail API via OAuth2. Na primeira execução abre o browser
    para o usuário autorizar com mkmarcoslopes@gmail.com. Nas seguintes
    usa o token salvo em gmail_token.json automaticamente.
    """
    creds = None

    if os.path.exists(GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print("🌐 Abrindo browser para autorização OAuth do Gmail...")
            print("   Faça login com mkmarcoslopes@gmail.com e autorize o acesso.")
            flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDENTIALS_FILE, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)

        with open(GMAIL_TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _extrair_corpo_email(msg):
    """Extrai todo o texto do e-mail percorrendo recursivamente as partes."""
    textos = []

    def _decode(data):
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _coletar(parte):
        data = parte.get("body", {}).get("data", "")
        if data:
            textos.append(_decode(data))
        for sub in parte.get("parts", []):
            _coletar(sub)

    try:
        payload = msg.get("payload", {})
        _coletar(payload)
    except Exception:
        pass

    return "\n".join(textos) or msg.get("snippet", "")


def _normalizar_busca_email(texto):
    texto = texto or ""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return texto.lower()


def _extrair_codigo_2fa_confiavel(from_h, subject_h, corpo):
    """Valida se o e-mail parece ser 2FA do Canal Pro e extrai um codigo de 6 digitos."""
    from_l = _normalizar_busca_email(from_h)
    subject_l = _normalizar_busca_email(subject_h)
    corpo_l = _normalizar_busca_email(corpo)

    blacklist_from = [
        "novidades.zapimoveis.com.br",
        "news@",
        "newsletter",
        "marketing",
        "noreply@mail.",
        "noreply@email.",
        "ofertas@",
        "promocao@",
        "comunicacao@",
    ]
    whitelist_from = [
        "grupozap", "canalpro", "canal-pro", "olx.com.br",
        "zapimoveis.com.br", "vivareal.com", "olx.com",
    ]
    assuntos_auth = [
        "confirmacao", "codigo de confirmacao", "verificacao",
        "acesso", "autenticacao", "codigo para", "uso unico",
        "canal pro", "grupo olx", "novo dispositivo",
    ]
    corpo_auth = [
        "confirmar", "uso unico", "novo dispositivo",
        "canal pro", "codigo", "verificar",
    ]
    promo_keywords = [
        "chegou o imovel", "perfeito pra voce", "oferta", "promocao",
        "desconto", "novidade", "imovel perfeito",
    ]

    if any(b in from_l for b in blacklist_from):
        return None, f"remetente bloqueado: {from_h[:60]}"
    if any(p in subject_l for p in promo_keywords):
        return None, f"assunto promocional: {subject_h[:60]}"

    assunto_ok = any(a in subject_l for a in assuntos_auth)
    from_ok = any(w in from_l for w in whitelist_from)
    corpo_ok = any(c in corpo_l for c in corpo_auth)
    if not (assunto_ok or from_ok) or not corpo_ok:
        return None, f"nao parece e-mail de autenticacao (assunto={subject_h[:40]}, from={from_h[:40]})"

    padroes = [
        r"confirmar[:\s]+(\d{6})",
        r"codigo[:\s]*(\d{6})",
        r"use o codigo[:\s]*(\d{6})",
        r"\b(\d{6})\b",
    ]
    for padrao in padroes:
        m = re.search(padrao, corpo_l, re.IGNORECASE)
        if m:
            return m.group(1), "ok"

    return None, "e-mail de autenticacao sem codigo de 6 digitos"


def _extrair_corpo_email_imap(msg):
    textos = []
    partes = msg.walk() if msg.is_multipart() else [msg]
    for parte in partes:
        content_type = parte.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        try:
            textos.append(parte.get_content())
        except Exception:
            payload = parte.get_payload(decode=True) or b""
            charset = parte.get_content_charset() or "utf-8"
            textos.append(payload.decode(charset, errors="ignore"))
    return "\n".join(textos)


def _gmail_buscar_codigo_2fa_imap(janela_segundos=300, timestamp_inicio=None):
    """
    Fallback sem OAuth para quando gmail_token.json expira/revoga.
    Requer GMAIL_APP_PASSWORD no .env da VPS.
    """
    usuario = os.getenv("GMAIL_IMAP_EMAIL") or os.getenv("GMAIL_EMAIL") or CANALPRO_EMAIL
    senha_app = os.getenv("GMAIL_APP_PASSWORD") or os.getenv("GMAIL_IMAP_PASSWORD")
    if not senha_app:
        print("   ⚠️ Fallback IMAP indisponível: GMAIL_APP_PASSWORD não configurado.")
        return None
    senha_app = re.sub(r"\s+", "", senha_app.strip())

    ts_corte = timestamp_inicio or (datetime.now() - timedelta(seconds=janela_segundos))
    since_date = (ts_corte - timedelta(days=1)).strftime("%d-%b-%Y")

    try:
        print("   📧 Tentando fallback Gmail IMAP para buscar código 2FA...")
        with imaplib.IMAP4_SSL("imap.gmail.com", 993) as mail:
            mail.login(usuario, senha_app)
            mail.select("INBOX")
            status, data = mail.search(None, f'(SINCE "{since_date}")')
            if status != "OK" or not data or not data[0]:
                print("   📭 IMAP: nenhum e-mail recente encontrado.")
                return None

            ids = data[0].split()[-40:]
            for msg_id in reversed(ids):
                status, fetched = mail.fetch(msg_id, "(RFC822)")
                if status != "OK" or not fetched:
                    continue
                raw = fetched[0][1]
                msg = BytesParser(policy=policy.default).parsebytes(raw)

                data_email = msg.get("Date")
                try:
                    dt_email = parsedate_to_datetime(data_email)
                    if dt_email and dt_email.timestamp() < ts_corte.timestamp():
                        continue
                    data_str = dt_email.astimezone().strftime("%H:%M:%S") if dt_email else "?"
                except Exception:
                    data_str = "?"

                from_h = msg.get("From", "")
                subject_h = msg.get("Subject", "")
                corpo = _extrair_corpo_email_imap(msg)
                print(f"   📧 IMAP candidato: {data_str} | de='{from_h[:50]}' | assunto='{subject_h[:50]}'")

                codigo, motivo = _extrair_codigo_2fa_confiavel(from_h, subject_h, corpo)
                if codigo:
                    print("   ✅ Código 2FA encontrado via IMAP.")
                    return codigo
                print(f"   ❌ IMAP rejeitado: {motivo}")

    except Exception as exc:
        print(f"   ⚠️ Fallback IMAP falhou: {type(exc).__name__} | {repr(exc)}")

    return None


def _gmail_buscar_codigo_2fa(service, janela_segundos=300, timestamp_inicio=None):
    """
    Busca o código 2FA do Canal Pro com filtros fortes:
    - Ignora remetentes de marketing/newsletter
    - Exige assunto/conteúdo compatível com autenticação
    - Aceita apenas e-mails recebidos APÓS o início do login 2FA
    - Extrai código apenas perto de expressões de confirmação
    Retorna string de 6 dígitos ou None (nunca retorna código duvidoso).
    """
    # Remetentes/domínios de marketing a ignorar explicitamente
    BLACKLIST_FROM = [
        "novidades.zapimoveis.com.br",
        "news@",
        "newsletter",
        "marketing",
        "noreply@mail.",
        "noreply@email.",
        "ofertas@",
        "promocao@",
        "comunicacao@",
    ]
    # Remetentes/domínios confiáveis para autenticação
    WHITELIST_FROM = [
        "grupozap", "canalpro", "canal-pro", "olx.com.br",
        "zapimoveis.com.br", "vivareal.com", "olx.com",
    ]
    # Assuntos que indicam autenticação (ao menos um deve estar presente)
    ASSUNTOS_AUTH = [
        "confirmação", "confirmacao", "código de confirmação",
        "codigo de confirmacao", "verificação", "verificacao",
        "acesso", "autenticação", "codigo para", "uso único",
        "canal pro", "grupo olx", "novo dispositivo",
    ]
    # Termos no corpo que confirmam ser um e-mail de autenticação
    CORPO_AUTH = [
        "confirmar", "uso único", "novo dispositivo",
        "canal pro", "código", "codigo", "verificar",
    ]
    # Padrões de extração — específicos primeiro, genérico por último
    PADROES = [
        r"confirmar[:\s]+(\d{6})",
        r"código[:\s]*(\d{6})",
        r"codigo[:\s]*(\d{6})",
        r"use o código[:\s]*(\d{6})",
    ]

    def _e_email_auth(from_h, subject_h, corpo):
        from_l    = from_h.lower()
        subject_l = subject_h.lower()
        corpo_l   = corpo.lower()

        # Rejeitar blacklist
        if any(b in from_l for b in BLACKLIST_FROM):
            return False, f"remetente bloqueado: {from_h[:60]}"

        # Rejeitar assuntos promocionais óbvios
        promo_keywords = ["chegou o imóvel", "perfeito pra você", "oferta", "promoção",
                          "desconto", "novidade", "imóvel perfeito"]
        if any(p in subject_l for p in promo_keywords):
            return False, f"assunto promocional: {subject_h[:60]}"

        # Exigir ao menos um termo de autenticação no assunto OU remetente confiável
        assunto_ok = any(a in subject_l for a in ASSUNTOS_AUTH)
        from_ok    = any(w in from_l for w in WHITELIST_FROM)
        corpo_ok   = any(c in corpo_l for c in CORPO_AUTH)

        if not (assunto_ok or from_ok) or not corpo_ok:
            return False, f"não parece e-mail de autenticação (assunto={subject_h[:40]}, from={from_h[:40]})"

        return True, "ok"

    try:
        minutos = max(2, janela_segundos // 60)

        queries = [
            f'subject:"confirmação" newer_than:{minutos}m',
            f'subject:"confirmacao" newer_than:{minutos}m',
            f'subject:"codigo" newer_than:{minutos}m',
            f'from:grupozap newer_than:{minutos}m',
            f'from:canalpro newer_than:{minutos}m',
        ]

        ids_vistos = set()
        mensagens_refs = []
        for query in queries:
            resultado = service.users().messages().list(
                userId="me", q=query, maxResults=10
            ).execute()
            for msg_ref in resultado.get("messages", []):
                if msg_ref["id"] not in ids_vistos:
                    ids_vistos.add(msg_ref["id"])
                    mensagens_refs.append(msg_ref)

        if not mensagens_refs:
            return None

        # Busca detalhes e filtra por timestamp_inicio
        ts_corte = int(timestamp_inicio.timestamp() * 1000) if timestamp_inicio else 0
        msgs_com_data = []
        for msg_ref in mensagens_refs:
            try:
                msg = service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="full"
                ).execute()
                internal_date = int(msg.get("internalDate", 0))
                if internal_date < ts_corte:
                    continue  # e-mail anterior ao login 2FA — ignorar
                msgs_com_data.append((internal_date, msg))
            except Exception:
                pass

        if not msgs_com_data:
            print("   📭 Nenhum e-mail de autenticação encontrado após o início do 2FA.")
            return None

        msgs_com_data.sort(key=lambda x: x[0], reverse=True)

        for data_ts, msg in msgs_com_data:
            headers = {h["name"].lower(): h["value"]
                       for h in msg.get("payload", {}).get("headers", [])}
            from_h    = headers.get("from", "")
            subject_h = headers.get("subject", "")
            corpo     = _extrair_corpo_email(msg)
            data_str  = datetime.fromtimestamp(data_ts / 1000).strftime("%H:%M:%S")

            print(f"   📧 Candidato: {data_str} | de='{from_h[:50]}' | assunto='{subject_h[:50]}'")

            valido, motivo = _e_email_auth(from_h, subject_h, corpo)
            if not valido:
                print(f"   ❌ Rejeitado: {motivo}")
                continue

            print(f"   ✅ E-mail de autenticação aceito.")
            print(f"   📄 Corpo (200 chars): {corpo[:200]}")

            # Tenta padrões específicos primeiro
            for padrao in PADROES:
                m = re.search(padrao, corpo, re.IGNORECASE)
                if m:
                    codigo = m.group(1)
                    print(f"✅ Código 2FA encontrado: {codigo}")
                    return codigo

            # Fallback: qualquer 6 dígitos — mas SOMENTE se e-mail passou na validação
            m = re.search(r"\b(\d{6})\b", corpo)
            if m:
                codigo = m.group(1)
                print(f"✅ Código 2FA encontrado (fallback): {codigo}")
                return codigo

            print("   ⚠️ E-mail passou na validação mas não contém código de 6 dígitos.")

        print("❌ 2FA_CODE_NOT_FOUND_CONFIDENTLY — nenhum código confiável encontrado.")
        return None

    except Exception as exc:
        print(f"⚠️ Erro ao buscar código no Gmail: {type(exc).__name__} | {repr(exc)}")
        return None


def _canal_pro_handle_cookie_popup():
    """
    Tenta fechar o banner de consentimento de cookies do Canal Pro.
    Usa 4 camadas em ordem de prioridade. Silencioso se não encontrar.
    """
    print("🍪 Procurando pop-up de cookies...")

    def _try_click_btn(btn, camada):
        try:
            if not btn.is_displayed():
                return False
            try:
                safe_click(btn)
            except Exception:
                driver.execute_script("arguments[0].click();", btn)
            time.sleep(1.5)
            # Confirma fechamento: se o botão ainda estiver visível, tenta JS
            try:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
            except Exception:
                pass
            print(f"🍪 Pop-up de cookies fechado com sucesso (Camada {camada}).")
            return True
        except Exception:
            return False

    # CAMADA 1 — Seletor exato pelo texto "Salvar opções" (Adopt banner)
    try:
        btn = driver.find_element(By.XPATH, "//button[normalize-space(text())='Salvar opções']")
        if _try_click_btn(btn, 1):
            return
    except Exception:
        pass

    # CAMADA 2 — Prefixo de classe "adopt-c-"
    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, "button[class^='adopt-c-']")
        for btn in candidates:
            txt = (btn.text or "").strip().lower()
            if "salvar" in txt or "aceitar" in txt or "ok" in txt:
                if _try_click_btn(btn, 2):
                    return
        # Fallback: último visível dos candidatos adopt-c-
        for btn in reversed(candidates):
            if _try_click_btn(btn, 2):
                return
    except Exception:
        pass

    # CAMADA 3 — Seletores genéricos de banners
    generic_selectors = [
        "button[class*='cookie']",
        "button[class*='consent']",
        "button[id*='cookie']",
        "button[class*='accept']",
        "button[class*='lgpd']",
    ]
    generic_texts = {
        "aceitar", "aceitar todos", "accept", "ok", "concordo",
        "entendi", "salvar", "continuar", "rejeitar", "fechar"
    }
    try:
        for sel in generic_selectors:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                if _try_click_btn(btn, 3):
                    return
            except Exception:
                pass
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            try:
                if (btn.text or "").strip().lower() in generic_texts:
                    if _try_click_btn(btn, 3):
                        return
            except Exception:
                pass
    except Exception:
        pass

    # CAMADA 4 — Busca dentro de iframes
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            try:
                driver.switch_to.frame(iframe)
                # Tenta Camadas 1-3 dentro do iframe
                for sel in ["//button[normalize-space(text())='Salvar opções']"]:
                    try:
                        btn = driver.find_element(By.XPATH, sel)
                        if _try_click_btn(btn, 4):
                            driver.switch_to.default_content()
                            return
                    except Exception:
                        pass
                for btn in driver.find_elements(By.TAG_NAME, "button"):
                    try:
                        if (btn.text or "").strip().lower() in generic_texts:
                            if _try_click_btn(btn, 4):
                                driver.switch_to.default_content()
                                return
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
    except Exception:
        pass

    print("🍪 Nenhum pop-up de cookies detectado. Prosseguindo.")


def _mask_value(value):
    value = value or ""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def _safe_attr(el, attr):
    try:
        return el.get_attribute(attr) or ""
    except Exception:
        return ""


def _element_summary(el):
    try:
        outer = _safe_attr(el, "outerHTML")
        return {
            "tag": (el.tag_name or "").lower(),
            "type": _safe_attr(el, "type"),
            "name": _safe_attr(el, "name"),
            "id": _safe_attr(el, "id"),
            "placeholder": _safe_attr(el, "placeholder"),
            "aria-label": _safe_attr(el, "aria-label"),
            "autocomplete": _safe_attr(el, "autocomplete"),
            "text": (el.text or "").strip()[:300],
            "href": _safe_attr(el, "href"),
            "visible": el.is_displayed(),
            "enabled": el.is_enabled(),
            "value": _mask_value(_safe_attr(el, "value")),
            "outerHTML": outer[:1000],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def save_debug_snapshot(driver_ref, label):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "snapshot"
    out_dir = os.path.join("debug", f"{ts}_{safe_label}")
    os.makedirs(out_dir, exist_ok=True)

    metadata = {
        "timestamp": datetime.now().isoformat(),
        "label": label,
        "ci": os.getenv("CI"),
        "modo_headless": str(MODO_HEADLESS),
        "pythonutf8": os.getenv("PYTHONUTF8"),
        "pythonioencoding": os.getenv("PYTHONIOENCODING"),
    }
    try:
        metadata.update({
            "current_url": driver_ref.current_url,
            "title": driver_ref.title,
            "window_size": driver_ref.get_window_size(),
            "user_agent": driver_ref.execute_script("return navigator.userAgent"),
            "ready_state": driver_ref.execute_script("return document.readyState"),
            "handles": driver_ref.window_handles,
            "num_abas": len(driver_ref.window_handles),
            "page_source_length": len(driver_ref.page_source or ""),
        })
    except Exception as exc:
        metadata["metadata_error"] = f"{type(exc).__name__}: {exc}"

    try:
        driver_ref.save_screenshot(os.path.join(out_dir, "screenshot.png"))
    except Exception as exc:
        metadata["screenshot_error"] = f"{type(exc).__name__}: {exc}"

    try:
        with open(os.path.join(out_dir, "page.html"), "w", encoding="utf-8") as f:
            f.write(driver_ref.page_source or "")
    except Exception as exc:
        metadata["html_error"] = f"{type(exc).__name__}: {exc}"

    def _dump(name, elements):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump([_element_summary(el) for el in elements], f, ensure_ascii=False, indent=2)

    try:
        _dump("inputs.json", driver_ref.find_elements(By.CSS_SELECTOR, "input, select, textarea"))
        _dump("buttons.json", driver_ref.find_elements(By.CSS_SELECTOR, "button, [role='button'], input[type='submit']"))
        _dump("links.json", driver_ref.find_elements(By.CSS_SELECTOR, "a"))
    except Exception as exc:
        metadata["elements_error"] = f"{type(exc).__name__}: {exc}"

    with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"🧪 Debug snapshot salvo: {out_dir}")
    return out_dir


def _visible_enabled(elements):
    result = []
    for el in elements:
        try:
            if el.is_displayed() and el.is_enabled():
                result.append(el)
        except Exception:
            pass
    return result


def _scroll_to_element(el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.2)


def _find_by_selectors(css_selectors=None, xpaths=None, timeout=30):
    end = time.time() + timeout
    css_selectors = css_selectors or []
    xpaths = xpaths or []
    last_seen = None
    while time.time() < end:
        for sel in css_selectors:
            try:
                found = _visible_enabled(driver.find_elements(By.CSS_SELECTOR, sel))
                if found:
                    _scroll_to_element(found[0])
                    return found[0]
            except Exception:
                pass
        for xp in xpaths:
            try:
                found = _visible_enabled(driver.find_elements(By.XPATH, xp))
                if found:
                    _scroll_to_element(found[0])
                    return found[0]
            except Exception:
                pass
        try:
            last_seen = len(driver.find_elements(By.CSS_SELECTOR, "input, textarea, select"))
        except Exception:
            last_seen = None
        time.sleep(1)
    print(f"⚠️ Elemento não localizado após {timeout}s. Inputs vistos: {last_seen}")
    return None


def find_password_input(driver_ref, timeout=30):
    return _find_by_selectors(
        css_selectors=[
            "input[type='password']",
            "input[name*='password' i]",
            "input[name*='senha' i]",
            "input[id*='password' i]",
            "input[id*='senha' i]",
            "input[placeholder*='senha' i]",
            "input[placeholder*='password' i]",
            "input[autocomplete='current-password']",
        ],
        xpaths=[
            "//*[self::input or self::textarea][contains(translate(@placeholder,'SENHAPASSWORD','senhapassword'),'senha')]",
            "//*[self::input or self::textarea][contains(translate(@placeholder,'SENHAPASSWORD','senhapassword'),'password')]",
            "//*[self::input or self::textarea][contains(translate(@name,'SENHAPASSWORD','senhapassword'),'senha')]",
            "//*[self::input or self::textarea][contains(translate(@id,'SENHAPASSWORD','senhapassword'),'senha')]",
        ],
        timeout=timeout,
    )


def find_email_input(driver_ref, timeout=30):
    el = _find_by_selectors(
        css_selectors=[
            "input[type='email']",
            "input[name='email']",
            "input[name='login']",
            "input[name='username']",
            "input[id*='email' i]",
            "input[id*='login' i]",
            "input[placeholder*='email' i]",
            "input[placeholder*='e-mail' i]",
            "input[aria-label*='email' i]",
            "input[autocomplete='email']",
            "input.l-input__item[type='text']",
        ],
        xpaths=[
            "//*[self::input or self::textarea][contains(translate(@placeholder,'EMAIL','email'),'email')]",
            "//*[self::input or self::textarea][contains(translate(@aria-label,'EMAIL','email'),'email')]",
            "//*[self::input or self::textarea][contains(translate(@name,'EMAIL','email'),'email')]",
            "//*[self::input or self::textarea][contains(translate(@id,'EMAIL','email'),'email')]",
        ],
        timeout=timeout,
    )
    if el:
        return el

    inputs = _visible_enabled(driver_ref.find_elements(By.CSS_SELECTOR, "input, textarea"))
    password = find_password_input(driver_ref, timeout=2)
    if password and inputs:
        for idx, candidate in enumerate(inputs):
            if candidate == password and idx > 0:
                return inputs[idx - 1]
    if len(inputs) >= 2:
        return inputs[0]
    return None


def find_submit_button(driver_ref, timeout=15):
    return _find_by_selectors(
        css_selectors=["button[type='submit']", "input[type='submit']"],
        xpaths=[
            "//button[contains(translate(normalize-space(.),'ENTRARLOGINACESSARCONTINUAR','entrarloginacessarcontinuar'),'entrar')]",
            "//button[contains(translate(normalize-space(.),'ENTRARLOGINACESSARCONTINUAR','entrarloginacessarcontinuar'),'login')]",
            "//button[contains(translate(normalize-space(.),'ENTRARLOGINACESSARCONTINUAR','entrarloginacessarcontinuar'),'acessar')]",
            "//button[contains(translate(normalize-space(.),'ENTRARLOGINACESSARCONTINUAR','entrarloginacessarcontinuar'),'continuar')]",
        ],
        timeout=timeout,
    )


def detect_canal_pro_page_state(driver_ref):
    try:
        url = (driver_ref.current_url or "").lower()
        title = (driver_ref.title or "").lower()
        body = (driver_ref.find_element(By.TAG_NAME, "body").text or "").lower()
        source_len = len(driver_ref.page_source or "")
        visible_inputs = _visible_enabled(driver_ref.find_elements(By.CSS_SELECTOR, "input, textarea"))
        has_password = bool(_visible_enabled(driver_ref.find_elements(By.CSS_SELECTOR, "input[type='password'], input[name*='password' i], input[name*='senha' i]")))
        has_email = bool(find_email_input(driver_ref, timeout=1))
    except Exception:
        return "UNKNOWN"

    if "captcha" in body or "cloudflare" in body or "access denied" in body or "403" in title or "429" in body:
        return "CAPTCHA_OR_BLOCK"
    if "performance/home" in url or "/listings" in url or "anúncios" in body or "anuncios" in body:
        return "ALREADY_LOGGED_IN"
    if "acesso em um novo dispositivo" in body or "verificação" in body or "verificacao" in body or "código" in body or "codigo" in body:
        return "TWO_FACTOR"
    if "cookie" in body and any(t in body for t in ["aceitar", "salvar", "consent"]):
        return "COOKIE_MODAL"
    if has_email and has_password:
        return "LOGIN_FORM"
    if has_password and len(visible_inputs) <= 2:
        return "PASSWORD_ONLY"
    if source_len < 300 or not body.strip():
        return "BLANK"
    if "carregando" in body or "loading" in body:
        return "LOADING"
    return "UNKNOWN"


def _canal_pro_login():
    """
    Abre nova aba, faz login no Canal Pro e retorna o handle anterior.
    O login do Canal Pro muda com frequência; por isso este fluxo detecta
    estados de página, salva evidências e usa localizadores tolerantes.
    """
    aba_crm = driver.current_window_handle
    print("🔐 Abrindo nova aba para o Canal Pro...")
    driver.execute_script("window.open('');")
    driver.switch_to.window(driver.window_handles[-1])

    def _preencher_campo(campo, valor, nome):
        try:
            _scroll_to_element(campo)
            campo.click()
            campo.clear()
            campo.send_keys(valor)
        except Exception:
            print(f"   ⚠️ send_keys falhou para campo {nome}. Usando fallback JavaScript...")
            driver.execute_script(
                "arguments[0].focus();"
                "arguments[0].value = arguments[1];"
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('blur', {bubbles: true}));",
                campo, valor
            )
            print(f"   ✅ Campo {nome} preenchido via JavaScript.")

        valor_atual = campo.get_attribute("value") or ""
        if valor_atual != valor:
            driver.execute_script(
                "arguments[0].focus();"
                "arguments[0].value = arguments[1];"
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('blur', {bubbles: true}));",
                campo, valor
            )
            valor_atual = campo.get_attribute("value") or ""
            if valor_atual != valor:
                raise Exception(f"Falha ao preencher campo '{nome}'. Esperado: '{valor}', obtido: '{valor_atual}'")

    ultimo_snapshot = None
    ultimo_erro = None

    for tentativa in range(1, 4):
        try:
            print(f"🔐 Login Canal Pro tentativa {tentativa}/3...")
            driver.get(CANAL_PRO_URL_LOGIN)
            WebDriverWait(driver, 30).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
            try:
                driver.set_window_size(1920, 1080)
            except Exception:
                pass
            time.sleep(3)

            for _ in range(2):
                _canal_pro_handle_cookie_popup()
                time.sleep(0.5)

            driver.execute_script(
                "document.querySelectorAll('[class*=\"adopt-c-\"], [class*=\"cookie-overlay\"], [class*=\"backdrop\"]')"
                ".forEach(el => { if (el.tagName !== 'BUTTON') el.style.display = 'none'; });"
            )

            estado = detect_canal_pro_page_state(driver)
            print(f"🔎 Estado detectado no Canal Pro: {estado} | URL={driver.current_url} | title={driver.title}")

            if estado == "ALREADY_LOGGED_IN":
                if validate_canal_pro_logged_in(driver):
                    print(f"✅ Canal Pro já estava logado. URL: {driver.current_url}")
                    return aba_crm

            if estado in ("UNKNOWN", "BLANK", "LOADING", "COOKIE_MODAL"):
                ultimo_snapshot = save_debug_snapshot(driver, f"canal_pro_estado_{estado}_tentativa_{tentativa}")
                if estado == "COOKIE_MODAL":
                    _canal_pro_handle_cookie_popup()
                driver.refresh()
                time.sleep(4)
                estado = detect_canal_pro_page_state(driver)
                print(f"🔎 Estado após refresh: {estado}")

            if estado == "CAPTCHA_OR_BLOCK":
                ultimo_snapshot = save_debug_snapshot(driver, f"canal_pro_bloqueio_tentativa_{tentativa}")
                raise Exception(f"ERROR_CANAL_PRO_LOGIN: bloqueio/captcha detectado. Evidências: {ultimo_snapshot}")

            if estado == "TWO_FACTOR":
                _canal_pro_handle_2fa()
                if validate_canal_pro_logged_in(driver):
                    return aba_crm

            print("📝 Localizando campo de e-mail/login...")
            email_field = find_email_input(driver, timeout=30)
            if not email_field:
                ultimo_snapshot = save_debug_snapshot(driver, f"canal_pro_email_nao_encontrado_tentativa_{tentativa}")
                raise Exception(f"Campo de e-mail não encontrado. Evidências: {ultimo_snapshot}")

            print("📝 Localizando campo de senha...")
            senha_field = find_password_input(driver, timeout=30)
            if not senha_field:
                ultimo_snapshot = save_debug_snapshot(driver, f"canal_pro_senha_nao_encontrada_tentativa_{tentativa}")
                raise Exception(f"Campo de senha não encontrado. Evidências: {ultimo_snapshot}")

            print(f"📝 Preenchendo e-mail: {CANALPRO_EMAIL}")
            _preencher_campo(email_field, CANALPRO_EMAIL, "email")
            print("📝 Preenchendo senha: [oculta]")
            _preencher_campo(senha_field, CANALPRO_SENHA, "senha")

            print("🖱️ Submetendo login...")
            btn_entrar = find_submit_button(driver, timeout=8)
            if btn_entrar:
                try:
                    safe_click(btn_entrar)
                except Exception:
                    driver.execute_script("arguments[0].click();", btn_entrar)
            else:
                senha_field.send_keys(Keys.RETURN)

            print("⏳ Aguardando resposta pós-login...")
            status = _canal_pro_aguardar_pos_login()
            if status == "2fa":
                print("🔐 Detectada tela de 2FA (verificação em duas etapas).")
                _canal_pro_handle_2fa()
            elif status != "ok":
                ultimo_snapshot = save_debug_snapshot(driver, f"canal_pro_pos_submit_inesperado_tentativa_{tentativa}")
                raise Exception(f"Login Canal Pro: estado inesperado pós-submit. Evidências: {ultimo_snapshot}")

            if validate_canal_pro_logged_in(driver):
                print(f"✅ Login no Canal Pro realizado. URL: {driver.current_url}")
                return aba_crm

            ultimo_snapshot = save_debug_snapshot(driver, f"canal_pro_validacao_pos_login_falhou_tentativa_{tentativa}")
            raise Exception(f"Login Canal Pro: validação pós-login falhou. Evidências: {ultimo_snapshot}")

        except Exception as exc:
            ultimo_erro = exc
            print(f"⚠️ Tentativa {tentativa}/3 de login Canal Pro falhou: {type(exc).__name__} | {exc}")
            if tentativa < 3:
                try:
                    driver.delete_all_cookies()
                except Exception:
                    pass
                time.sleep(3)

    raise Exception(
        "ERROR_CANAL_PRO_LOGIN: login do Canal Pro falhou após 3 tentativas. "
        f"Último erro: {ultimo_erro}. Últimas evidências: {ultimo_snapshot}"
    )


def validate_canal_pro_logged_in(driver_ref):
    try:
        url = (driver_ref.current_url or "").lower()
        if "canalpro.grupozap.com" not in url and "canal-pro.grupozap.com" not in url:
            return False
        login_inputs = _visible_enabled(driver_ref.find_elements(
            By.CSS_SELECTOR,
            "input[type='password'], input[name='email'], input[autocomplete='email']"
        ))
        if login_inputs and "login" in url:
            return False
        _canal_pro_navigate_to_listings()
        WebDriverWait(driver_ref, 20).until(
            lambda d: "/listings" in (d.current_url or "").lower()
            or len(d.find_elements(By.CSS_SELECTOR, "span.card-content__tag")) > 0
            or _canal_pro_lista_vazia_confirmada()[0]
            or bool(_canal_pro_obter_contador_oficial_texto())
        )
        return True
    except Exception:
        save_debug_snapshot(driver_ref, "canal_pro_validate_logged_in_falhou")
        return False


def _canal_pro_aguardar_pos_login():
    """Aguarda redirecionamento pós-login OU detecta tela de 2FA. Retorna 'ok', '2fa' ou None."""
    def _check(d):
        url = d.current_url or ""
        if "performance/home" in url or "listings" in url:
            return "ok"
        try:
            body_text = d.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            return False
        if "Acesso em um novo dispositivo" in body_text:
            return "2fa"
        if "informe o código" in body_text.lower():
            return "2fa"
        return False

    try:
        return WebDriverWait(driver, 15).until(_check)
    except TimeoutException:
        return None


def _canal_pro_clicar_verificar_codigo():
    """Clica em 'Verificar código' e aguarda o redirecionamento pós-2FA."""
    for by, seletor in [
        (By.XPATH, "//button[normalize-space(text())='Verificar código']"),
        (By.XPATH, "//button[contains(normalize-space(.), 'Verificar')]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
    ]:
        try:
            btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, seletor)))
            try:
                btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", btn)
            print("🖱️ Botão 'Verificar código' clicado.")
            WebDriverWait(driver, 25).until(
                lambda d: "performance/home" in d.current_url or "listings" in d.current_url
            )
            print("✅ Código 2FA validado. Login no Canal Pro concluído.")
            return
        except Exception:
            continue
    snap = save_debug_snapshot(driver, "canal_pro_2fa_verificar_codigo_falhou")
    raise Exception(f"2FA: não foi possível clicar em 'Verificar código' ou aguardar redirecionamento. Evidências: {snap}")


def _canal_pro_preencher_codigo_2fa(codigo):
    """Localiza os 6 campos do código 2FA e preenche dígito a dígito."""
    print(f"📝 Preenchendo código 2FA: {codigo}")
    inputs = []

    for seletor, nome in [
        ("input[maxlength='1']", "maxlength=1"),
        ("input[type='tel']", "type=tel"),
        ("input[type='number']", "type=number"),
        ("input[class*='otp'], input[class*='code'], input[class*='pin']", "otp/code/pin"),
    ]:
        try:
            found = driver.find_elements(By.CSS_SELECTOR, seletor)
            visiveis = [el for el in found if el.is_displayed()]
            if len(visiveis) >= 6:
                inputs = visiveis[:6]
                print(f"   ✅ Campos encontrados via: {nome}")
                break
        except Exception:
            continue

    if not inputs:
        try:
            primeiro = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//input[@maxlength='1' or @type='tel' or @type='number']")
                )
            )
            inputs = [primeiro]
        except Exception:
            pass

    if not inputs:
        html = driver.execute_script("return document.body.innerHTML.slice(0, 5000);")
        print("⚠️ Campos do código 2FA não encontrados. HTML parcial:")
        print(html[:2000])
        snap = save_debug_snapshot(driver, "canal_pro_2fa_campos_nao_encontrados")
        raise Exception(f"2FA: campos de código não encontrados. Evidências: {snap}")

    # Tenta auto-tab (envia código completo no primeiro campo)
    try:
        inputs[0].click()
        inputs[0].send_keys(codigo)
        time.sleep(0.5)
        if len(inputs) >= 6:
            valores = "".join((inp.get_attribute("value") or "").strip() for inp in inputs)
            if valores == codigo:
                print("✅ Código preenchido via auto-tab.")
                _canal_pro_clicar_verificar_codigo()
                return
    except Exception:
        pass

    # Preenche dígito a dígito
    for i, digito in enumerate(codigo):
        if i >= len(inputs):
            break
        campo = inputs[i]
        try:
            campo.clear()
            campo.send_keys(digito)
            time.sleep(0.15)
        except Exception:
            driver.execute_script(
                "arguments[0].focus();"
                "arguments[0].value = arguments[1];"
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                campo, digito
            )
            time.sleep(0.15)

    print("✅ Código preenchido dígito a dígito.")
    _canal_pro_clicar_verificar_codigo()


def _canal_pro_handle_2fa():
    """
    Trata autenticação 2FA do Canal Pro via Gmail API OAuth2.
    Registra o momento exato em que o 2FA foi disparado para filtrar
    apenas e-mails chegados DEPOIS disso, evitando newsletters antigas.
    """
    print("🔐 Autenticação 2FA detectada — buscando código no Gmail...")
    # Subtrai 3 minutos: o email é enviado quando "Entrar" é clicado,
    # antes da tela de 2FA aparecer. O buffer evita rejeitar o código válido.
    timestamp_inicio = datetime.now() - timedelta(minutes=3)

    print("📧 Autenticando Gmail API...")
    gmail_service = None
    erro_gmail_api = None
    try:
        gmail_service = _gmail_autenticar()
        print("✅ Gmail API autenticado.")
    except Exception as exc:
        erro_gmail_api = exc
        print(f"⚠️ Gmail API indisponível: {repr(exc)}")
        print("   Vou tentar o fallback IMAP com GMAIL_APP_PASSWORD.")

    TIMEOUT_SEGUNDOS   = 180
    INTERVALO_SEGUNDOS = 10
    inicio = time.time()
    tentativa = 0

    while time.time() - inicio < TIMEOUT_SEGUNDOS:
        tentativa += 1
        restante = int(TIMEOUT_SEGUNDOS - (time.time() - inicio))
        print(f"   🔍 Tentativa #{tentativa} — buscando código 2FA... ({restante}s restantes)")

        janela = int(time.time() - inicio) + 30
        codigo = None
        if gmail_service:
            codigo = _gmail_buscar_codigo_2fa(
                gmail_service,
                janela_segundos=max(janela, 60),
                timestamp_inicio=timestamp_inicio,
            )
        if not codigo:
            codigo = _gmail_buscar_codigo_2fa_imap(
                janela_segundos=max(janela, 60),
                timestamp_inicio=timestamp_inicio,
            )

        if codigo and len(codigo) == 6 and codigo.isdigit():
            print(f"✅ Código 2FA obtido automaticamente: {codigo}")
            _canal_pro_preencher_codigo_2fa(codigo)
            return

        time.sleep(INTERVALO_SEGUNDOS)

    detalhe_api = f" Gmail API falhou antes: {repr(erro_gmail_api)}" if erro_gmail_api else ""
    raise Exception(f"ERROR_2FA: código não encontrado no Gmail após 180s.{detalhe_api}")


def _canal_pro_navigate_to_listings():
    """
    Navega para a página de Anúncios via menu hamburguer.
    Fallback: navega diretamente pela URL.
    """
    print("📋 Navegando para a página de Anúncios...")
    _canal_pro_handle_cookie_popup()

    try:
        # Clica no hamburguer
        hamburger = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button#menu-burger-button"))
        )
        safe_click(hamburger)
        time.sleep(0.8)

        # Clica em Anúncios
        anuncios_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button#menu-anuncios-button"))
        )
        safe_click(anuncios_btn)

        # Confirma que a página carregou
        WebDriverWait(driver, 15).until(
            lambda d: "/listings" in d.current_url or
            len(d.find_elements(By.CSS_SELECTOR, "span.card-content__tag")) > 0
        )
        time.sleep(2)
        _canal_pro_handle_cookie_popup()
        print("✅ Página de Anúncios carregada.")

    except Exception as exc:
        print(f"⚠️ Navegação pelo menu falhou: {type(exc).__name__}. Tentando URL direta...")
        driver.get(CANAL_PRO_URL_LISTINGS)
        time.sleep(3)
        _canal_pro_handle_cookie_popup()
        print("✅ Página de Anúncios carregada (fallback URL).")


def _canal_pro_obter_contador_oficial_texto():
    seletores = [
        "div.pagination span",
        "div.pagination",
        "[data-testid='pagination']",
        "span.pagination__label",
    ]
    for sel in seletores:
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            try:
                txt = (el.text or "").strip()
                if re.search(r"\d+\s*-\s*\d+\s*de\s*\d+", txt.lower()) or re.search(r"\b\d+\s*de\s*\d+\b", txt.lower()):
                    return txt
            except Exception:
                pass
    body = (driver.find_element(By.TAG_NAME, "body").text or "")
    m = re.search(r"\b\d+\s*-\s*\d+\s*de\s*\d+\b", body, flags=re.IGNORECASE)
    if m:
        return m.group(0)
    return ""


def _canal_pro_lista_vazia_confirmada():
    body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    frases_vazio = [
        "nenhum anúncio",
        "nenhum anuncio",
        "0 anúncios",
        "0 anuncios",
        "nenhum resultado",
        "sem anúncios",
        "sem anuncios",
    ]
    if any(f in body_text for f in frases_vazio):
        return True, "mensagem_oficial_lista_vazia"

    contador = (_canal_pro_obter_contador_oficial_texto() or "").lower()
    if re.search(r"\b0\s*-\s*0\s*de\s*0\b", contador) or re.search(r"\b0\s*de\s*0\b", contador):
        return True, f"contador_oficial={contador}"

    return False, "sem_confirmacao_oficial_de_lista_vazia"


def sessao_canal_pro_expirada(driver_ref):
    try:
        url = (driver_ref.current_url or "").lower()
    except Exception:
        return True

    if "login" in url or "auth" in url:
        return True

    if driver_ref.find_elements(By.CSS_SELECTOR, "input[type='password'], input[name='password']"):
        return True
    if driver_ref.find_elements(By.CSS_SELECTOR, "input[name='email'], input[type='email']"):
        return True

    tem_cards = len(driver_ref.find_elements(By.CSS_SELECTOR, "span.card-content__tag")) > 0
    contador = _canal_pro_obter_contador_oficial_texto()
    if not tem_cards and not contador:
        body_text = (driver_ref.find_element(By.TAG_NAME, "body").text or "").lower()
        if any(t in body_text for t in ["entrar", "autenticação", "autenticacao", "verificar código", "verificar codigo"]):
            return True

    return False


def coletar_codigos_pagina_com_retry(driver_ref, pagina_atual, max_tentativas=3):
    motivo_final = "indefinido"
    for tentativa in range(1, max_tentativas + 1):
        try:
            WebDriverWait(driver_ref, 12).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "span.card-content__tag")) > 0
                or "Criado em" in d.page_source
                or _canal_pro_lista_vazia_confirmada()[0]
            )
        except Exception:
            pass

        codigos = set()
        cards = driver_ref.find_elements(By.CSS_SELECTOR, "span.card-content__tag")
        for el in cards:
            try:
                texto = (el.text or "").strip()
                if texto.isdigit():
                    codigos.add(texto)
            except Exception:
                pass

        if not codigos:
            html = driver_ref.page_source or ""
            padroes_html = [
                # Layout novo do Canal Pro em /anuncios: tag numerica antes do preco e datas.
                r"<span>\s*(\d{2,6})\s*</span>\s*</div>\s*</div>\s*</header>\s*<div[^>]*>\s*<h3[^>]*>\s*R\$",
                # Fallback mais tolerante: codigo numerico pequeno dentro de um card que contem "Criado em".
                r"<span>\s*(\d{2,6})\s*</span>.{0,900}?Criado em",
            ]
            for padrao in padroes_html:
                for codigo in re.findall(padrao, html, flags=re.IGNORECASE | re.DOTALL):
                    codigos.add(str(codigo).strip())

        if codigos:
            return {
                "sucesso": True,
                "codigos": codigos,
                "lista_vazia_confirmada": False,
                "motivo": f"codigos_coletados_na_tentativa_{tentativa}",
            }

        lista_vazia, motivo_vazio = _canal_pro_lista_vazia_confirmada()
        if lista_vazia:
            return {
                "sucesso": True,
                "codigos": set(),
                "lista_vazia_confirmada": True,
                "motivo": motivo_vazio,
            }

        motivo_final = (
            f"pagina_{pagina_atual}_retornou_0_codigos_sem_confirmacao_oficial_de_lista_vazia"
            f"_tentativa_{tentativa}"
        )
        if tentativa < max_tentativas:
            print(f"   ⚠️ Página {pagina_atual} retornou 0 códigos (tentativa {tentativa}/{max_tentativas}). Aguardando 3s...")
            time.sleep(3)
            driver_ref.execute_script("window.scrollTo(0, 300);")
            time.sleep(1)
            driver_ref.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)

    return {
        "sucesso": False,
        "codigos": set(),
        "lista_vazia_confirmada": False,
        "motivo": motivo_final,
    }


def _canal_pro_botao_proxima_habilitado():
    seletores = [
        "button[aria-label='Próxima Página']",
        "button[aria-label='Próxima pagina']",
        "button[aria-label='Next Page']",
        "button.pagination__button--next",
    ]
    for sel in seletores:
        elems = driver.find_elements(By.CSS_SELECTOR, sel)
        if not elems:
            continue
        btn = elems[0]
        classe = (btn.get_attribute("class") or "").lower()
        disabled = (btn.get_attribute("disabled") or "").lower()
        habilitado = btn.is_enabled() and "disabled" not in classe and disabled not in ("true", "disabled")
        return btn, habilitado
    return None, False


def _canal_pro_ir_para_pagina_1():
    url = CANAL_PRO_URL_LISTINGS
    if "?" in url:
        url += "&pageNumber=1"
    else:
        url += "?pageNumber=1"
    driver.get(url)
    time.sleep(2)
    _canal_pro_handle_cookie_popup()


def _canal_pro_collect_all_active_codes(ultimo_total_valido=0):
    all_codes = set()
    page = 1
    paginas_ok = 0
    paginas_falhas = 0
    lista_vazia_confirmada_global = False

    _canal_pro_ir_para_pagina_1()

    while True:
        resultado_pagina = coletar_codigos_pagina_com_retry(driver, pagina_atual=page, max_tentativas=3)
        if not resultado_pagina["sucesso"]:
            paginas_falhas += 1
            return {
                "erro_scraping": True,
                "motivo": resultado_pagina["motivo"],
                "codigos_ativos": all_codes,
                "total_codigos_ativos": len(all_codes),
                "paginas_sucesso": paginas_ok,
                "paginas_falhas": paginas_falhas,
                "lista_vazia_confirmada_global": False,
            }

        paginas_ok += 1
        codigos_pagina = resultado_pagina["codigos"]
        if resultado_pagina["lista_vazia_confirmada"] and page == 1:
            lista_vazia_confirmada_global = True

        print(f"   📄 Página {page}: {len(codigos_pagina)} código(s) coletado(s): {sorted(codigos_pagina)}")
        all_codes.update(codigos_pagina)

        btn_next, habilitado = _canal_pro_botao_proxima_habilitado()
        if not habilitado:
            break
        safe_click(btn_next)
        time.sleep(2)
        page += 1

    total = len(all_codes)
    if paginas_ok < 1:
        return {
            "erro_scraping": True,
            "motivo": "nenhuma_pagina_processada_com_sucesso",
            "codigos_ativos": all_codes,
            "total_codigos_ativos": total,
            "paginas_sucesso": paginas_ok,
            "paginas_falhas": paginas_falhas,
            "lista_vazia_confirmada_global": lista_vazia_confirmada_global,
        }

    if total < MINIMO_CODIGOS_ESPERADOS_CANAL_PRO and not lista_vazia_confirmada_global:
        return {
            "erro_scraping": True,
            "motivo": f"total_suspeito_abaixo_do_minimo({total}<{MINIMO_CODIGOS_ESPERADOS_CANAL_PRO})",
            "codigos_ativos": all_codes,
            "total_codigos_ativos": total,
            "paginas_sucesso": paginas_ok,
            "paginas_falhas": paginas_falhas,
            "lista_vazia_confirmada_global": lista_vazia_confirmada_global,
        }

    if ultimo_total_valido >= MINIMO_CODIGOS_ESPERADOS_CANAL_PRO and total == 0 and not lista_vazia_confirmada_global:
        return {
            "erro_scraping": True,
            "motivo": f"queda_improvavel_de_{ultimo_total_valido}_para_0_sem_confirmacao_oficial",
            "codigos_ativos": all_codes,
            "total_codigos_ativos": total,
            "paginas_sucesso": paginas_ok,
            "paginas_falhas": paginas_falhas,
            "lista_vazia_confirmada_global": lista_vazia_confirmada_global,
        }

    return {
        "erro_scraping": False,
        "motivo": "varredura_valida",
        "codigos_ativos": all_codes,
        "total_codigos_ativos": total,
        "paginas_sucesso": paginas_ok,
        "paginas_falhas": paginas_falhas,
        "lista_vazia_confirmada_global": lista_vazia_confirmada_global,
    }


def _avaliar_resultado_intermediario(codigos_alvo, resultado_varredura, ultimo_total_valido):
    ativos = set(resultado_varredura.get("codigos_ativos") or set())
    total_atual = int(resultado_varredura.get("total_codigos_ativos", len(ativos)))
    lista_vazia_confirmada = bool(resultado_varredura.get("lista_vazia_confirmada_global"))
    erro_scraping = bool(resultado_varredura.get("erro_scraping"))
    motivo = resultado_varredura.get("motivo", "sem_motivo")

    if not erro_scraping and ultimo_total_valido >= MINIMO_CODIGOS_ESPERADOS_CANAL_PRO and total_atual == 0 and not lista_vazia_confirmada:
        erro_scraping = True
        motivo = f"queda_improvavel_de_{ultimo_total_valido}_para_0_sem_confirmacao_oficial"

    ainda_ativos = set(codigos_alvo) & ativos
    return {
        "erro_scraping": erro_scraping,
        "motivo": motivo,
        "ativos": ativos,
        "total_atual": total_atual,
        "ainda_ativos": ainda_ativos,
        "todos_removidos": len(ainda_ativos) == 0 and not erro_scraping,
    }


def _deve_abortar_por_erros_consecutivos(erros_consecutivos, max_erros=MAX_ERROS_CONSECUTIVOS_SCRAPING):
    return erros_consecutivos >= max_erros


def _test_canal_pro_login_flow():
    print("🧪 TESTE SEGURO: login Canal Pro + Anúncios, sem alterar imóveis.")
    aba_origem = _canal_pro_login()
    _canal_pro_navigate_to_listings()
    resultado = coletar_codigos_pagina_com_retry(driver, pagina_atual=1, max_tentativas=3)
    snap = save_debug_snapshot(driver, "canal_pro_login_test_success")
    codigos = sorted(resultado.get("codigos") or [])
    print(f"✅ Teste Canal Pro concluído. Página 1: {len(codigos)} código(s): {codigos}")
    print(f"🧪 Snapshot de sucesso: {snap}")
    try:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(aba_origem)
    except Exception:
        pass
    return True


def _prevalidate_target_portal_before_mutation():
    portal = require_portal_target_config()
    print(f"PREVALIDACAO: portal alvo = {portal_target_label()}")
    if not go_to_imoveis_page_fresh():
        raise Exception("Prevalidacao falhou: nao abriu tela de imoveis.")
    apply_initial_filters()
    botoes = driver.find_elements(By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")
    print(f"PREVALIDACAO: filtro do portal alvo retornou {len(botoes)} botao(oes) editar.")
    if botoes:
        safe_click(botoes[0])
        wait.until(EC.visibility_of_element_located((By.ID, "titulo-input")))
        codigo = get_property_code_from_modal()
        if not open_divulgacao_tab(portal["id"]):
            raise Exception(f"Prevalidacao falhou: portal alvo {portal_target_label()} nao existe na aba Divulgacao.")
        checked = is_target_portal_checked(portal["id"])
        print(f"PREVALIDACAO: imovel amostra={codigo} portal marcado={checked}")
        close_any_open_modal()
        close_known_popup_modals()
    driver.get("https://www.rioorla.com.br/crm/po.php")
    time.sleep(2)
    btn = find_target_portal_update_button(portal["id"], portal["name"], portal["file"])
    print(f"PREVALIDACAO: botao updatePortais encontrado: {_portal_button_summary(btn)}")
    if os.getenv("VALIDAR_CANAL_PRO_PRE_MUTATION", "true").lower() in ("1", "true", "yes"):
        _test_canal_pro_login_flow()


def _selftest_parte_intermediaria():
    alvo = {"1018", "1146"}

    c1 = _avaliar_resultado_intermediario(
        codigos_alvo=alvo,
        resultado_varredura={
            "erro_scraping": True,
            "motivo": "pagina_1_retornou_0_sem_confirmacao",
            "codigos_ativos": set(),
            "total_codigos_ativos": 0,
            "lista_vazia_confirmada_global": False,
        },
        ultimo_total_valido=0,
    )
    assert c1["erro_scraping"] is True and c1["todos_removidos"] is False

    c2 = _avaliar_resultado_intermediario(
        codigos_alvo=alvo,
        resultado_varredura={
            "erro_scraping": False,
            "motivo": "varredura_valida",
            "codigos_ativos": set(),
            "total_codigos_ativos": 0,
            "lista_vazia_confirmada_global": False,
        },
        ultimo_total_valido=83,
    )
    assert c2["erro_scraping"] is True

    c3 = _avaliar_resultado_intermediario(
        codigos_alvo=alvo,
        resultado_varredura={
            "erro_scraping": False,
            "motivo": "varredura_valida",
            "codigos_ativos": {"1018", "9999"},
            "total_codigos_ativos": 20,
            "lista_vazia_confirmada_global": False,
        },
        ultimo_total_valido=20,
    )
    assert c3["todos_removidos"] is False and c3["erro_scraping"] is False

    c4 = _avaliar_resultado_intermediario(
        codigos_alvo=alvo,
        resultado_varredura={
            "erro_scraping": False,
            "motivo": "varredura_valida",
            "codigos_ativos": {"9999"},
            "total_codigos_ativos": 15,
            "lista_vazia_confirmada_global": False,
        },
        ultimo_total_valido=20,
    )
    assert c4["todos_removidos"] is True and c4["erro_scraping"] is False

    erros = 0
    abortou = False
    for _ in range(5):
        erros += 1
        if _deve_abortar_por_erros_consecutivos(erros, 5):
            abortou = True
            break
    assert abortou is True

    print("✅ Self-test Parte Intermediária: cenários críticos validados.")


def verify_properties_removed_from_zap(imoveis_processados):
    """
    Parte Intermediária: abre o Canal Pro em nova aba e verifica a cada
    VERIFICACAO_INTERVALO_SEGUNDOS se os imóveis da Parte 1 foram removidos
    dos anúncios ativos. Só avança quando TODOS estiverem removidos.
    Timeout máximo: VERIFICACAO_TIMEOUT_SEGUNDOS.
    """
    if not imoveis_processados:
        raise Exception("ETAPA INTERMEDIÁRIA sem imóveis da ETAPA 1 da execução atual. Parte 2 bloqueada.")

    codigos_alvo = {str(item["codigo"]).strip() for item in imoveis_processados}
    print(f"\n🔍 Parte Intermediária: monitorando remoção de {len(codigos_alvo)} imóvel(is) no ZAP Imóveis...")
    print(f"   Códigos aguardados: {sorted(codigos_alvo)}")
    print(f"   Intervalo entre verificações: {VERIFICACAO_INTERVALO_SEGUNDOS // 60} minuto(s)\n")

    aba_crm = _canal_pro_login()
    try:
        _canal_pro_navigate_to_listings()
    except Exception:
        pass

    inicio = time.time()
    tentativa = 1

    try:
        erros_consecutivos = 0
        ultimo_total_valido = 0
        while True:
            # Verifica timeout
            if time.time() - inicio > VERIFICACAO_TIMEOUT_SEGUNDOS:
                print(f"⛔ TIMEOUT: imóveis não foram removidos do ZAP em "
                      f"{VERIFICACAO_TIMEOUT_SEGUNDOS // 3600} hora(s). Encerrando.")
                raise TimeoutError(f"Timeout de {VERIFICACAO_TIMEOUT_SEGUNDOS // 3600}h na verificação do Canal Pro.")

            horario = datetime.now().strftime("%H:%M:%S")
            print(f"🔍 [{horario}] Verificação #{tentativa} — varrendo anúncios no Canal Pro...")

            if sessao_canal_pro_expirada(driver):
                print("⚠️ Sessão do Canal Pro expirada. Refazendo login...")
                aba_crm = _canal_pro_login()
                _canal_pro_navigate_to_listings()

            try:
                resultado = _canal_pro_collect_all_active_codes(ultimo_total_valido=ultimo_total_valido)
            except Exception as exc:
                print(f"⚠️ Erro ao coletar códigos: {type(exc).__name__} | {repr(exc)}")
                resultado = {"erro_scraping": True, "motivo": f"excecao_{type(exc).__name__}", "codigos_ativos": set(), "total_codigos_ativos": 0}

            avaliacao = _avaliar_resultado_intermediario(
                codigos_alvo=codigos_alvo,
                resultado_varredura=resultado,
                ultimo_total_valido=ultimo_total_valido,
            )

            if avaliacao.get("erro_scraping"):
                erros_consecutivos += 1
                contador = _canal_pro_obter_contador_oficial_texto()
                cards = len(driver.find_elements(By.CSS_SELECTOR, "span.card-content__tag"))
                lista_vazia, _ = _canal_pro_lista_vazia_confirmada()
                login_visivel = sessao_canal_pro_expirada(driver)
                print("⛔ VERIFICAÇÃO INVÁLIDA")
                print(f"   Motivo: {avaliacao.get('motivo')}")
                print(f"   URL atual: {driver.current_url}")
                print(f"   Cards encontrados: {cards}")
                print(f"   Contador oficial: {contador or 'não encontrado'}")
                print(f"   Tela de login detectada: {login_visivel}")
                print(f"   Mensagem oficial de lista vazia: {lista_vazia}")
                print(f"   Total coletado: {avaliacao.get('total_atual', 0)}")
                print(f"   Último total válido conhecido: {ultimo_total_valido}")
                print("   A Parte 2 NÃO será executada.")

                if erros_consecutivos >= MAX_ERROS_CONSECUTIVOS_SCRAPING:
                    pendentes = sorted(codigos_alvo)
                    msg = (
                        "PARTE_INTERMEDIARIA_ABORTADA: erros consecutivos de scraping no Canal Pro. "
                        "Parte 2 não executada. Imóveis podem estar desmarcados no CRM/VivaReal e exigem atenção manual. "
                        f"Códigos pendentes: {pendentes}"
                    )
                    raise Exception(msg)

                print(f"   ⚠️ Erros consecutivos: {erros_consecutivos}/{MAX_ERROS_CONSECUTIVOS_SCRAPING}")
                print(f"   ⏱️ Nova tentativa em {VERIFICACAO_INTERVALO_SEGUNDOS // 60} minuto(s).")
                time.sleep(VERIFICACAO_INTERVALO_SEGUNDOS)
                tentativa += 1
                _canal_pro_navigate_to_listings()
                continue

            erros_consecutivos = 0
            ativos = avaliacao["ativos"]
            total_ativos = avaliacao["total_atual"]
            if total_ativos >= MINIMO_CODIGOS_ESPERADOS_CANAL_PRO or resultado.get("lista_vazia_confirmada_global"):
                ultimo_total_valido = total_ativos

            print(f"   📊 Total de códigos ativos no Canal Pro: {total_ativos}")

            ainda_ativos = avaliacao["ainda_ativos"]
            ja_removidos = codigos_alvo - ativos

            if ja_removidos:
                print(f"   ✅ Já removidos do ZAP: {sorted(ja_removidos)}")

            if not ainda_ativos:
                print("✅ TODOS os imóveis confirmados como removidos do ZAP Imóveis!")
                print("🔒 Fechando aba do Canal Pro e retornando ao CRM...\n")
                return True

            proxima = datetime.now().strftime("%H:%M:%S")
            print(f"   ⏳ Ainda ativos no ZAP ({len(ainda_ativos)} imóvel(is)): {sorted(ainda_ativos)}")
            print(f"   ⏱️ Próxima verificação em {VERIFICACAO_INTERVALO_SEGUNDOS // 60} minuto(s)... [{proxima}]")
            time.sleep(VERIFICACAO_INTERVALO_SEGUNDOS)
            tentativa += 1

            # Renavega para listings (sem novo login)
            try:
                _canal_pro_navigate_to_listings()
            except Exception as exc:
                print(f"⚠️ Falha ao renavegar — tentando login novamente: {type(exc).__name__}")
                try:
                    aba_crm = _canal_pro_login()
                    _canal_pro_navigate_to_listings()
                except Exception:
                    pass

    finally:
        # Garante que a aba do Canal Pro seja fechada e o driver volte ao CRM
        # independentemente de sucesso ou exceção (2FA, timeout, etc.)
        try:
            if len(driver.window_handles) > 1:
                driver.close()
                driver.switch_to.window(aba_crm)
        except Exception:
            pass






# =============================================================================
# PARTE 2 — REMARCAR VIVAREAL
# =============================================================================

def _process_single_item_parte2(item):
    codigo = (item.get("codigo") or "").strip()
    categoria_value = str(item.get("categoria_portal", item.get("categoria_vivareal", "0"))).strip() or "0"
    categoria_nome = item.get("categoria_nome") or get_vivareal_category_label(categoria_value)

    if categoria_value not in CATEGORIAS_VIVAREAL:
        categoria_value = "0"
        categoria_nome = "Simples"

    if not codigo:
        print("⚠️ Item sem código. Pulando.")
        return False

    print(f"🔎 Parte 2: iniciando ciclo limpo para código {codigo}")

    if not search_property_by_code_strict(codigo):
        return False

    edit_property_result_by_code(codigo)

    if not open_divulgacao_tab():
        raise Exception("Não consegui abrir Divulgação dentro do imóvel correto.")

    set_target_portal_checked(True)
    set_target_portal_category_value(categoria_value)
    save_property()
    print(f"Parte 2 concluida para {codigo}: {portal_target_label()} marcado como {categoria_nome} ({categoria_value}).")
    close_any_open_modal()
    return True


def process_part_2_restore_vivareal(imoveis_processados):
    if not imoveis_processados:
        print("ℹ️ Nenhum imóvel salvo na Parte 1. Parte 2 será ignorada.")
        return [], []

    restaurados_parte2 = []
    falhas_parte2 = []

    for item in imoveis_processados:
        codigo = (item.get("codigo") or "").strip()
        try:
            ok = _process_single_item_parte2(item)
            if ok:
                restaurados_parte2.append(item)
            else:
                falhas_parte2.append(item)
        except Exception as exc:
            print(f"⚠️ Erro ao restaurar imóvel de código {codigo}: {type(exc).__name__} | {repr(exc)}")
            debug_modal_state(f"erro_parte2_codigo_{codigo}")
            close_any_open_modal()
            falhas_parte2.append(item)
            continue

    if falhas_parte2:
        print(f"🔁 Reprocessando {len(falhas_parte2)} falhas da Parte 2...")
        pendentes = list(falhas_parte2)
        falhas_parte2 = []
        for item in pendentes:
            codigo = (item.get("codigo") or "").strip()
            try:
                ok = _process_single_item_parte2(item)
                if ok:
                    restaurados_parte2.append(item)
                else:
                    falhas_parte2.append(item)
            except Exception as exc:
                print(f"⚠️ Erro no reprocessamento do código {codigo}: {type(exc).__name__} | {repr(exc)}")
                debug_modal_state(f"erro_reprocesso_codigo_{codigo}")
                close_any_open_modal()
                falhas_parte2.append(item)

    if falhas_parte2:
        print(f"ATENCAO: os seguintes imoveis nao foram restaurados em {portal_target_label()}:")
        for item in falhas_parte2:
            print(f"- Codigo {item['codigo']} | Categoria {item['categoria_nome']} ({item.get('categoria_portal', item.get('categoria_vivareal'))})")

    return restaurados_parte2, falhas_parte2


# =============================================================================
# PROXY
# =============================================================================

def _criar_extensao_proxy_auth(host, porta, usuario, senha):
    """
    Cria uma extensão Chrome temporária que injeta as credenciais do proxy
    automaticamente. Necessário porque Chrome não aceita user:pass na URL do
    proxy via linha de comando — a extensão responde ao evento onAuthRequired.
    """
    manifest = json.dumps({
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage",
            "<all_urls>", "webRequest", "webRequestBlocking"
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0"
    })

    background = f"""
var config = {{
    mode: "fixed_servers",
    rules: {{
        singleProxy: {{ scheme: "http", host: "{host}", port: parseInt({porta}) }},
        bypassList: ["localhost", "127.0.0.1"]
    }}
}};
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function(){{}});

chrome.webRequest.onAuthRequired.addListener(
    function(details) {{
        return {{ authCredentials: {{ username: "{usuario}", password: "{senha}" }} }};
    }},
    {{urls: ["<all_urls>"]}},
    ["blocking"]
);
"""

    ext_path = os.path.join(tempfile.gettempdir(), "proxy_auth_ext.zip")
    with zipfile.ZipFile(ext_path, "w") as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("background.js", background)

    return ext_path


# =============================================================================
# ROLLBACK DE EMERGÊNCIA + RESUMO FINAL
# =============================================================================

def _tentar_rollback_se_necessario(imoveis_processados, ts_str):
    """
    Chamado nos blocos except do main().
    Se algum imóvel foi desmarcado (checkpoint tem registros), tenta reverter.
    Gera arquivo de pendência se o rollback parcial falhar.
    """
    # Prioriza lista em memória; fallback para checkpoint em disco
    desmarcados = imoveis_processados or _checkpoint_carregar_desmarcados()
    if not desmarcados:
        return

    print(f"\n⚠️  {len(desmarcados)} imóvel(is) foram desmarcados antes do erro.")
    revertidos, pendentes = _rollback_automatico(desmarcados)

    if pendentes:
        arquivo = _gerar_arquivo_rollback_pendente(pendentes, ts_str)
        print(f"\n🚨 ROLLBACK PARCIAL — {len(pendentes)} imóvel(is) NÃO revertidos!")
        print(f"   Arquivo de pendência: {arquivo}")
        print(f"   ACAO MANUAL NECESSARIA: remarcar {portal_target_label()} para os codigos abaixo:")
        for item in pendentes:
            print(f"      -> {item['codigo']} | {item.get('categoria_nome','?')} ({item.get('categoria_portal', item.get('categoria_vivareal','?'))})")
        _checkpoint_fechar("ERROR_AFTER_MUTATION_ROLLBACK_PENDING")
    else:
        print(f"✅ Rollback concluído: {len(revertidos)} imóvel(is) restaurados.")
        try:
            print("🚀 Disparando sincronização do ZAP após rollback...")
            go_to_integracoes_parceiros_and_update_vivareal()
        except Exception as exc:
            print(f"⚠️ Sync ZAP pós-rollback falhou: {exc}")
        _checkpoint_fechar("ERROR_AFTER_MUTATION_ROLLBACK_OK")


def _imprimir_resumo(status, encontrados, restaurados, falhas, falhas_lista,
                     arquivo_rollback, inicio):
    """Imprime resumo estruturado no final — nunca oculta erros."""
    duracao = str(datetime.now() - inicio).split(".")[0]
    print("\n" + "=" * 60)
    print("📊 RESUMO FINAL DA EXECUÇÃO")
    print("=" * 60)
    print(f"  status_final            : {status}")
    print(f"  imóveis_encontrados     : {encontrados}")
    print(f"  imóveis_restaurados     : {restaurados}")
    print(f"  imóveis_falhas_parte2   : {falhas}")
    print(f"  duração                 : {duracao}")
    if arquivo_rollback:
        print(f"  ⚠️  rollback_pendente   : {arquivo_rollback}")
    if falhas_lista:
        print("  Códigos com falha:")
        for item in falhas_lista:
            print(f"    → {item['codigo']} | {item.get('categoria_nome','?')}")
    if status == "SUCCESS":
        print("\n✅ Execução concluída com SUCESSO.")
    elif status in ("SKIPPED_NO_ITEMS", "DRY_RUN"):
        print(f"\nℹ️  Execução encerrada: {status} (nenhuma alteração feita).")
    else:
        print(f"\n❌ Execução encerrada com STATUS DE ERRO: {status}")
        print("   Verifique os logs e o diretório 'state/' para detalhes.")
    print("=" * 60)


# =============================================================================
# AGENDAMENTO
# =============================================================================

def wait_until_target_time():
    """Aguarda até o horário alvo de execução (23:00 por padrão)."""
    now = datetime.now()
    target = now.replace(hour=ALVO_EXECUCAO_HORA, minute=ALVO_EXECUCAO_MINUTO, second=0, microsecond=0)
    if now > target:
        target += timedelta(days=1)
    delta = (target - now).total_seconds()
    if delta > 0:
        print(f"⏰ Modo espera interna ativo. Aguardando até {target.strftime('%d/%m/%Y %H:%M:%S')} para iniciar...")
        time.sleep(delta)
    print(f"🕙 {ALVO_EXECUCAO_HORA:02d}:{ALVO_EXECUCAO_MINUTO:02d} — iniciando execução.")


def _diagnostico_agendador_windows():
    """
    Diagnóstico do agendamento automático (Windows Task Scheduler).
    Não aborta a execução; imprime alertas para evitar falhas silenciosas.
    """
    if platform.system().lower() != "windows":
        print("ℹ️ Diagnóstico de agendador: sistema não-Windows, verificação do Task Scheduler ignorada.")
        return

    problemas = []
    py_exec = subprocess.run(
        ["where", "python"],
        capture_output=True, text=True, shell=True
    )
    python_path = (py_exec.stdout or "").strip().splitlines()
    python_path = python_path[0] if python_path else ""
    script_path = os.path.abspath("atualizacao_zap.py")
    cwd = os.path.abspath(".")
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    print("\n🧭 Diagnóstico do agendamento (Windows)")
    print(f"   Horário atual: {agora}")
    print(f"   Horário alvo: {ALVO_EXECUCAO_HORA:02d}:{ALVO_EXECUCAO_MINUTO:02d}")
    print(f"   Script principal: {script_path}")
    print(f"   Diretório atual: {cwd}")
    print(f"   Python detectado: {python_path or 'NÃO ENCONTRADO'}")

    if not os.path.exists(script_path):
        problemas.append("Script principal não encontrado.")
    if not python_path:
        problemas.append("Python não encontrado no PATH.")

    task = subprocess.run(
        ["schtasks", "/query", "/tn", SCHEDULED_TASK_NAME, "/fo", "LIST", "/v"],
        capture_output=True, text=True
    )
    if task.returncode != 0:
        problemas.append(f"Tarefa '{SCHEDULED_TASK_NAME}' não encontrada no Agendador.")
    else:
        out = task.stdout or ""
        def _extrair(rotulo):
            m = re.search(rf"{re.escape(rotulo)}\s*:\s*(.+)", out, flags=re.IGNORECASE)
            return m.group(1).strip() if m else "N/A"

        status = _extrair("Status")
        hora_inicio = _extrair("Hora de início")
        acao = _extrair("Tarefa a ser executada")
        iniciar_em = _extrair("Iniciar em")
        modo_logon = _extrair("Modo de Logon")
        ultima_exec = _extrair("Horário da última execução")
        ultimo_resultado = _extrair("Último resultado")

        print(f"   Tarefa encontrada: {SCHEDULED_TASK_NAME}")
        print(f"   Status: {status}")
        print(f"   Hora agendada: {hora_inicio}")
        print(f"   Comando: {acao}")
        print(f"   Iniciar em: {iniciar_em}")
        print(f"   Modo de logon: {modo_logon}")
        print(f"   Última execução: {ultima_exec}")
        print(f"   Último resultado: {ultimo_resultado}")

        if "desabilitado" in status.lower() or "desativado" in status.lower():
            problemas.append("Tarefa está desabilitada.")
        if "23:00:00" not in hora_inicio:
            problemas.append(f"Horário incorreto no agendador ({hora_inicio}); esperado 23:00:00.")
        if "executar_atualizacao_zap.bat" not in acao.lower() and "atualizacao_zap.py" not in acao.lower():
            problemas.append("A tarefa não aponta para o launcher/binary esperado do projeto Atualizacao_ZAP.")
        if iniciar_em.upper() == "N/A":
            problemas.append("Campo 'Iniciar em' não definido (pode quebrar caminhos relativos).")

    if problemas:
        print("⚠️ Diagnóstico detectou inconsistências:")
        for p in problemas:
            print(f"   - {p}")
    else:
        print("✅ Diagnóstico de agendamento: OK.")


# =============================================================================
# HEALTHCHECK / CHROME STARTUP / NOTIFICAÇÃO
# =============================================================================

def _healthcheck_inicial():
    problemas = []

    for arq in ["gmail_credentials.json", "gmail_token.json"]:
        if not os.path.exists(arq):
            problemas.append(f"Arquivo ausente: {arq}")

    try:
        _, _, free = shutil.disk_usage("/")
        if free < 500 * 1024 * 1024:
            problemas.append(f"Pouco espaço em disco: {free // 1024 // 1024}MB livres")
    except Exception:
        pass

    for path in ["/tmp", "/dev/shm"]:
        if os.path.exists(path):
            try:
                _, _, free_p = shutil.disk_usage(path)
                if free_p < 100 * 1024 * 1024:
                    problemas.append(f"Pouco espaço em {path}: {free_p // 1024 // 1024}MB")
            except Exception:
                pass

    if os.name != "nt":
        try:
            r = subprocess.run(["google-chrome", "--version"],
                               capture_output=True, text=True, timeout=5)
            print(f"   Chrome: {r.stdout.strip()}")
        except Exception as exc:
            problemas.append(f"google-chrome não encontrado: {exc}")

    if problemas:
        print("⚠️ HEALTHCHECK encontrou problemas:")
        for p in problemas:
            print(f"   - {p}")
        return False

    print("✅ Healthcheck OK — todos os pré-requisitos atendidos.")
    return True


def _healthcheck_completo():
    """
    Healthcheck seguro: valida ambiente e encerra sem executar ETAPAS 1/2.
    """
    print("🩺 HEALTHCHECK MODE -- sem alterações de dados.")
    ok = _healthcheck_inicial()
    if not ok:
        return False

    checks = [
        ("CRM_USUARIO", os.getenv("CRM_USUARIO")),
        ("CRM_SENHA", os.getenv("CRM_SENHA")),
        ("CANALPRO_EMAIL", os.getenv("CANALPRO_EMAIL")),
        ("CANALPRO_SENHA", os.getenv("CANALPRO_SENHA")),
        ("PORTAL_TARGET_ID", os.getenv("PORTAL_TARGET_ID")),
        ("PORTAL_TARGET_NAME", os.getenv("PORTAL_TARGET_NAME")),
        ("PORTAL_TARGET_FILE", os.getenv("PORTAL_TARGET_FILE")),
    ]
    faltantes = [k for k, v in checks if not v]
    if faltantes:
        print(f"⛔ Variáveis ausentes: {faltantes}")
        return False

    print(f"Portal alvo configurado: {portal_target_label()}")

    try:
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        os.makedirs("logs", exist_ok=True)
        teste_log = os.path.join("logs", "healthcheck.log")
        with open(teste_log, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} healthcheck ok\n")
    except Exception as exc:
        print(f"⛔ Falha ao escrever logs/state: {exc}")
        return False

    print("✅ HEALTHCHECK concluído com sucesso.")
    return True


def _iniciar_chrome_com_retry(options, usando_headless):
    MAX_TENTATIVAS = 5
    BACKOFF = [5, 10, 20, 40]

    ultimo_erro = None

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        print(f"🖥️  Tentativa {tentativa}/{MAX_TENTATIVAS} — iniciando Chrome...")
        print(f"   Argumentos: {options.arguments}")
        try:
            service = Service(ChromeDriverManager().install())
            d = webdriver.Chrome(service=service, options=options)
            d.set_page_load_timeout(30)
            _ = d.current_url
            v   = d.capabilities.get("browserVersion", "?")
            cdv = d.capabilities.get("chrome", {}).get("chromedriverVersion", "?")
            print(f"✅ Chrome iniciado na tentativa {tentativa}. "
                  f"Chrome {v} | ChromeDriver {str(cdv)[:30]}")
            return d
        except Exception as exc:
            ultimo_erro = exc
            print(f"⚠️ Tentativa {tentativa}/{MAX_TENTATIVAS} falhou:")
            print(f"   Tipo: {type(exc).__name__}")
            print(f"   Mensagem: {str(exc)[:500]}")
            print(traceback.format_exc()[:800])

            if os.name != "nt":
                try:
                    subprocess.run(["pkill", "-9", "-f", "chrome"],
                                   timeout=5, check=False, capture_output=True)
                    subprocess.run(["pkill", "-9", "-f", "chromedriver"],
                                   timeout=5, check=False, capture_output=True)
                    for f in ["/tmp/SingletonLock", "/tmp/SingletonCookie",
                               "/tmp/SingletonSocket"]:
                        try:
                            os.remove(f)
                        except OSError:
                            pass
                except Exception:
                    pass

            if tentativa < MAX_TENTATIVAS:
                espera = BACKOFF[tentativa - 1]
                print(f"   ⏳ Aguardando {espera}s antes da próxima tentativa...")
                time.sleep(espera)

    # Fallback final: sem proxy
    if usando_headless and any("--proxy-server" in a for a in options.arguments):
        print("⚠️ Tentando última vez SEM proxy como fallback de emergência...")
        try:
            opts_fb = Options()
            for arg in options.arguments:
                if "--proxy-server" not in arg:
                    opts_fb.add_argument(arg)
            service = Service(ChromeDriverManager().install())
            d = webdriver.Chrome(service=service, options=opts_fb)
            d.set_page_load_timeout(30)
            _ = d.current_url
            print("✅ Chrome iniciado SEM proxy (fallback de emergência).")
            return d
        except Exception as exc_fb:
            print(f"⛔ Fallback sem proxy também falhou: {exc_fb}")

    raise Exception(
        f"ERROR_BROWSER_STARTUP: Chrome não iniciou após {MAX_TENTATIVAS} tentativas. "
        f"Último erro: {type(ultimo_erro).__name__}: {ultimo_erro}"
    )


def _enviar_notificacao_final(status, inicio_execucao, encontrados, restaurados, codigos=None):
    try:
        from email.mime.text import MIMEText
        gmail_service = _gmail_autenticar()
        data    = datetime.now().strftime("%d/%m/%Y %H:%M")
        duracao = str(datetime.now() - inicio_execucao).split(".")[0]
        if status == "SUCCESS":
            icone, titulo = "✅", "SUCESSO"
        elif status.startswith("WARNING"):
            icone, titulo = "⚠️", f"ALERTA: {status}"
        else:
            icone, titulo = "❌", f"FALHA: {status}"
        assunto = f"{icone} Atualização ZAP — {titulo} ({datetime.now().strftime('%d/%m/%Y')})"
        corpo = (
            f"Execução do dia {data}\n\n"
            f"Status: {status}\n"
            f"Duração: {duracao}\n"
            f"Imóveis encontrados: {encontrados}\n"
            f"Imóveis restaurados: {restaurados}\n"
        )
        if codigos:
            corpo += f"\nCódigos processados: {', '.join(str(c) for c in sorted(codigos))}\n"
        if encontrados < EXPECTATIVA_MINIMA_PARTE_1 and encontrados > 0:
            corpo += f"\n⚠️ ALERTA: apenas {encontrados} imóvel(is) processados (esperado >= {EXPECTATIVA_MINIMA_PARTE_1}).\n"
        corpo += f"\nLogs na VPS: /opt/atualizacao-zap/logs/\n"
        msg = MIMEText(corpo)
        msg["to"]      = GMAIL_DESTINATARIO
        msg["subject"] = assunto
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail_service.users().messages().send(userId="me", body={"raw": raw}).execute()
        print("📧 Notificação enviada por e-mail.")
    except Exception as exc:
        print(f"⚠️ Falha ao enviar notificação: {exc}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    global driver, wait, actions

    em_nuvem = os.getenv("CI", "") == "true"
    teste_local = os.getenv("TEST_MODE", "") == "true"
    usar_espera_interna = os.getenv("USAR_ESPERA_INTERNA", "false").lower() == "true"
    run_id = _criar_run_context(teste_local=teste_local, em_nuvem=em_nuvem)
    _preparar_estado_inicio_execucao(modo_resume=MODO_PULAR_PARTE_1, teste_local=teste_local)
    _run_state_salvar(status="IN_PROGRESS", imoveis=[])
    resume_file_env = os.getenv("RESUME_FILE", "").strip()
    if MODO_PULAR_PARTE_1:
        imoveis_parte1_path_run = resume_file_env or IMOVEIS_PARTE1_PATH
    elif teste_local:
        imoveis_parte1_path_run = os.path.join(RUNS_DIR, f"imoveis_parte1_{run_id}.json")
    else:
        imoveis_parte1_path_run = IMOVEIS_PARTE1_PATH
    print(f"🗂️ Arquivo de estado da execução atual: {imoveis_parte1_path_run}")

    # Diagnóstico para alinhar código + Task Scheduler.
    _diagnostico_agendador_windows()

    # Com Task Scheduler diário, o padrão é iniciar imediatamente.
    # A espera interna só é usada quando explicitamente habilitada via env.
    if usar_espera_interna and not em_nuvem and not teste_local:
        print(f"🕒 Execução manual com espera interna habilitada para {ALVO_EXECUCAO_HORA:02d}:{ALVO_EXECUCAO_MINUTO:02d}.")
        wait_until_target_time()
    elif teste_local:
        print("🧪 MODO TESTE: execução imediata (espera interna desabilitada).")
    else:
        origem = "agendada/produção" if em_nuvem or not usar_espera_interna else "manual"
        print(f"🚀 Execução {origem}: iniciando imediatamente às {datetime.now().strftime('%H:%M:%S')}.")

    print("🔍 Verificando pré-requisitos...")
    if not _healthcheck_inicial():
        raise Exception("Healthcheck falhou — abortando antes de iniciar.")
    if HEALTHCHECK_ONLY:
        if not _healthcheck_completo():
            raise Exception("Healthcheck completo falhou.")
        return
    if os.getenv("SELFTEST_PARTE_INTERMEDIARIA", "false").lower() == "true":
        _selftest_parte_intermediaria()
        return

    options = Options()
    if AUDIT_PORTAL_UPDATE_ONLY:
        options.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-gpu")
    options.add_argument("--lang=pt-BR")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    )

    usando_headless = em_nuvem or MODO_HEADLESS

    if usando_headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        # Flags extras de estabilidade em servidor
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-translate")
        options.add_argument("--metrics-recording-only")
        options.add_argument("--mute-audio")
        options.add_argument("--no-first-run")
        options.add_argument("--safebrowsing-disable-auto-update")
        options.add_argument("--disable-features=VizDisplayCompositor,TranslateUI")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-ipc-flooding-protection")
    else:
        options.add_argument("--start-maximized")

    # Diretório temporário isolado por execução (evita conflito de lock files)
    temp_chrome_dir = tempfile.mkdtemp(prefix="chrome_session_")
    options.add_argument(f"--user-data-dir={temp_chrome_dir}")
    options.add_argument(f"--disk-cache-dir={temp_chrome_dir}/cache")

    if PROXY_ATIVO:
        print(f"🌐 Proxy ativo: {PROXY_HOST}:{PROXY_PORTA} (Brasil)")
        if usando_headless:
            options.add_argument(f"--proxy-server=http://{PROXY_HOST}:{PROXY_PORTA}")
            print("   ℹ️ Headless: proxy sem extensão (IP autorizado no WebShare).")
        else:
            options.add_argument(f"--proxy-server=http://{PROXY_HOST}:{PROXY_PORTA}")
            ext_path = _criar_extensao_proxy_auth(PROXY_HOST, PROXY_PORTA, PROXY_USUARIO, PROXY_SENHA)
            options.add_extension(ext_path)

    ts_str            = datetime.now().strftime("%Y%m%d_%H%M")
    inicio_execucao   = datetime.now()
    status_final      = "ERROR_UNKNOWN"
    imoveis_processados = []
    restaurados_parte2  = []
    falhas_parte2       = []
    arquivo_rollback    = None

    driver = _iniciar_chrome_com_retry(options, usando_headless)
    wait = WebDriverWait(driver, 30)
    actions = ActionChains(driver)
    try:
        driver.set_window_size(1920, 1080)
        ua = driver.execute_script("return navigator.userAgent")
        print(f"🖥️ Headless ativo: {usando_headless} | janela={driver.get_window_size()} | user-agent={ua}")
    except Exception as exc:
        print(f"⚠️ Não consegui registrar window/user-agent: {exc}")

    if DRY_RUN:
        print("🔍 DRY_RUN ATIVO — nenhuma alteração será feita no CRM.")

    try:
        # ── PRÉ-EXECUÇÃO: valida diretório de logs/checkpoints ────────────────
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        os.makedirs("logs", exist_ok=True)

        if TEST_CANAL_PRO_LOGIN_ONLY:
            _test_canal_pro_login_flow()
            status_final = "CANAL_PRO_LOGIN_TEST_OK"
            _run_state_salvar(status=status_final, imoveis=[])
            return

        # --- LOGIN CRM ---
        driver.get(CRM_URL)
        wait.until(EC.visibility_of_element_located((By.NAME, "usuario"))).send_keys(USUARIO)
        driver.find_element(By.NAME, "senha").send_keys(SENHA + Keys.RETURN)
        time.sleep(5)
        print("✅ Login realizado.")

        if AUDIT_PORTAL_UPDATE_ONLY:
            audit_dir = _audit_portal_update()
            status_final = "AUDIT_PORTAL_UPDATE_OK"
            _run_state_salvar(status=status_final, imoveis=[])
            print(f"🔎 Auditoria concluída sem executar Parte 1/Parte 2: {audit_dir}")
            return

        if AUDIT_PROPERTY_PORTAL_ONLY:
            audit_dir = _audit_property_portal(ARG_CODIGO)
            status_final = "AUDIT_PROPERTY_PORTAL_OK"
            _run_state_salvar(status=status_final, imoveis=[])
            print(f"Auditoria concluida sem executar Parte 1/Parte 2: {audit_dir}")
            return

        if MODO_PULAR_PARTE_1:
            # =====================================================================
            # MODO TESTE: pula Parte 1, retoma da Parte Intermediária
            # =====================================================================
            print("\n⏭️ MODO TESTE: pulando Parte 1 (já executada anteriormente).")
            print("   Lendo imoveis_parte1.json para retomar Parte Intermediária...")

            if not os.path.exists(imoveis_parte1_path_run):
                raise Exception(
                    f"Arquivo de resume não encontrado: {imoveis_parte1_path_run}. "
                    "Não é possível pular a Parte 1 sem esse arquivo."
                )

            with open(imoveis_parte1_path_run, "r", encoding="utf-8") as f:
                data = json.load(f)
                imoveis_processados = data.get("imoveis", [])
            status_resume = (data.get("status") or "").upper()
            run_id_resume = data.get("run_id")
            if status_resume == "SUCCESS":
                raise Exception(
                    "Resume bloqueado: imoveis_parte1.json pertence a execução concluída com SUCCESS. "
                    "Inicie execução normal para montar lista nova."
                )
            if not run_id_resume:
                raise Exception("Resume bloqueado: arquivo imoveis_parte1.json sem run_id.")

            print(f"📦 {len(imoveis_processados)} imóvel(is) carregados do JSON (run_id origem={run_id_resume}).")
            _run_state_salvar(status="RESUMING_PREVIOUS_RUN", imoveis=imoveis_processados)

        else:
            # =====================================================================
            # FLUXO NORMAL: executa Parte 1
            # =====================================================================
            if not go_to_imoveis_page_fresh():
                status_final = "ERROR_BEFORE_MUTATION"
                raise Exception("Não foi possível abrir Imóveis para iniciar a Parte 1.")

            _prevalidate_target_portal_before_mutation()

            if not go_to_imoveis_page_fresh():
                status_final = "ERROR_BEFORE_MUTATION"
                raise Exception("Não foi possível reabrir Imóveis após a pré-validação.")

            apply_initial_filters()

            # ── GUARDA DE 0 IMÓVEIS ──────────────────────────────────────────
            # Conta os botões de edição ANTES de processar para validar o filtro
            botoes_pre = driver.find_elements(By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")
            if len(botoes_pre) == 0:
                # Tenta recarregar os filtros uma vez antes de desistir
                print("⚠️ Nenhum imóvel encontrado na 1ª tentativa. Refazendo filtros...")
                time.sleep(3)
                apply_initial_filters()
                botoes_pre = driver.find_elements(By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")

            if len(botoes_pre) == 0:
                status_final = "SKIPPED_NO_ITEMS"
                print("\n⚠️ Nenhum imóvel elegível encontrado após 2 tentativas.")
                print("   Nenhuma alteração será feita. Encerrando como SKIPPED_NO_ITEMS.")
                _imprimir_resumo(status_final, 0, 0, 0, [], None, inicio_execucao)
                return  # sai do try normalmente, sem rollback

            if DRY_RUN:
                print(f"\n🔍 DRY_RUN: {len(botoes_pre)} imóvel(is) seriam processados. Nada alterado.")
                status_final = "DRY_RUN"
                _imprimir_resumo(status_final, len(botoes_pre), 0, 0, [], None, inicio_execucao)
                return

            # ── CRIA CHECKPOINT ANTES DE QUALQUER ALTERAÇÃO ──────────────────
            _checkpoint_criar(ts_str)

            print(f"\n🚧 ===== PARTE 1: desmarcando {portal_target_label()} =====")
            imoveis_processados = process_part_1_collect_and_disable_vivareal()
            print(f"📦 Total de imóveis salvos para a Parte 2: {len(imoveis_processados)}")
            print(f"🧾 ETAPA 1 ({run_id}) códigos desmarcados: {[str(i.get('codigo','')).strip() for i in imoveis_processados]}")
            _run_state_salvar(status="PARTE_1_CONCLUIDA", imoveis=imoveis_processados)

            with open(imoveis_parte1_path_run, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "run_id": run_id,
                        "mode": _RUN_CONTEXT.get("mode"),
                        "timestamp": datetime.now().isoformat(),
                        "status": "IN_PROGRESS",
                        "imoveis": imoveis_processados,
                    },
                    f, ensure_ascii=False, indent=2
                )
            print(f"💾 Estado da ETAPA 1 salvo para run_id={run_id}: {imoveis_parte1_path_run}")

            print(f"🚀 Atualizando {portal_target_label()} após Parte 1...")
            go_to_integracoes_parceiros_and_update_target_portal()

        # =====================================================================
        # PARTE INTERMEDIÁRIA
        # =====================================================================
        print("\n🔍 ===== PARTE INTERMEDIÁRIA: verificando remoção no ZAP Imóveis =====")
        print(f"🧾 ETAPA INTERMEDIÁRIA ({run_id}) verificando {len(imoveis_processados)} imóvel(is) da execução atual.")
        removidos_confirmados = verify_properties_removed_from_zap(imoveis_processados)
        if not removidos_confirmados:
            raise Exception("Parte 2 bloqueada: remoção no ZAP não foi confirmada.")
        _run_state_salvar(status="PARTE_INTERMEDIARIA_CONFIRMADA", imoveis=imoveis_processados)

        # =====================================================================
        # PARTE 2: remarcar portal alvo
        # =====================================================================
        print(f"\n🚧 ===== PARTE 2: restaurando {portal_target_label()} =====")
        print(f"🧾 ETAPA 2 ({run_id}) remarcará somente códigos da ETAPA 1: {[str(i.get('codigo','')).strip() for i in imoveis_processados]}")
        restaurados_parte2, falhas_parte2 = process_part_2_restore_vivareal(imoveis_processados)

        print(f"🚀 Atualizando {portal_target_label()} após Parte 2...")
        go_to_integracoes_parceiros_and_update_target_portal()

        if falhas_parte2:
            arquivo_rollback = _gerar_arquivo_rollback_pendente(falhas_parte2, ts_str)
            status_final = "ERROR_AFTER_MUTATION_ROLLBACK_PENDING"
            _run_state_salvar(status=status_final, imoveis=imoveis_processados)
        else:
            status_final = "SUCCESS"
            _checkpoint_fechar("SUCCESS")
            _run_state_salvar(status="SUCCESS", imoveis=imoveis_processados)
            if os.path.exists(imoveis_parte1_path_run):
                try:
                    with open(imoveis_parte1_path_run, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data["status"] = "SUCCESS"
                    data["closed_at"] = datetime.now().isoformat()
                    with open(imoveis_parte1_path_run, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception as exc:
                    print(f"⚠️ Falha ao marcar arquivo de estado como SUCCESS: {exc}")

    except (InvalidSessionIdException, WebDriverException) as exc:
        cod = "ERROR_BROWSER_STARTUP" if "ERROR_BROWSER_STARTUP" in repr(exc) else "ERROR_BROWSER"
        print(f"\n⛔ {cod}: {type(exc).__name__} | {repr(exc)[:200]}")
        status_final = cod
        _run_state_salvar(status=status_final, imoveis=imoveis_processados)
        _tentar_rollback_se_necessario(imoveis_processados, ts_str)

    except TimeoutError as exc:
        print(f"\n⛔ {exc}")
        status_final = "ERROR_TIMEOUT"
        _run_state_salvar(status=status_final, imoveis=imoveis_processados)
        _tentar_rollback_se_necessario(imoveis_processados, ts_str)

    except Exception as exc:
        msg = str(exc)
        if "ERROR_2FA" in msg:
            status_final = "ERROR_2FA"
        elif "ERROR_BROWSER_STARTUP" in msg:
            status_final = "ERROR_BROWSER_STARTUP"
        elif status_final == "ERROR_UNKNOWN":
            status_final = "ERROR_GENERAL"
        print(f"\n⛔ {status_final}: {type(exc).__name__} | {msg[:300]}")
        _run_state_salvar(status=status_final, imoveis=imoveis_processados)
        _tentar_rollback_se_necessario(imoveis_processados, ts_str)

    finally:
        if os.path.exists(imoveis_parte1_path_run):
            try:
                with open(imoveis_parte1_path_run, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("run_id") == run_id:
                    data["status"] = status_final
                    data["updated_at"] = datetime.now().isoformat()
                    with open(imoveis_parte1_path_run, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        _checkpoint_fechar(status_final)
        _imprimir_resumo(
            status_final,
            len(imoveis_processados),
            len(restaurados_parte2),
            len(falhas_parte2),
            falhas_parte2,
            arquivo_rollback,
            inicio_execucao,
        )
        codigos_processados = [str(i.get("codigo","")) for i in imoveis_processados if i.get("codigo")]
        status_notif = status_final
        if (status_final == "SUCCESS"
                and len(imoveis_processados) > 0
                and len(imoveis_processados) < EXPECTATIVA_MINIMA_PARTE_1):
            status_notif = "WARNING_POUCOS_IMOVEIS"
        if not AUDIT_PORTAL_UPDATE_ONLY:
            _enviar_notificacao_final(
                status_notif, inicio_execucao,
                len(imoveis_processados), len(restaurados_parte2),
                codigos=codigos_processados
            )
        if driver and (em_nuvem or os.getenv("FECHAR_BROWSER", "") == "1"):
            try:
                driver.quit()
            except Exception:
                pass
        try:
            shutil.rmtree(temp_chrome_dir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        # Evita UnicodeEncodeError em execuções agendadas com console cp1252.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
