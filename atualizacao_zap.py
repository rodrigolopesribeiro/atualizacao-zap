import base64
import json
import os
import re
from datetime import datetime, timedelta
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

# === CONFIGURACOES CRM ===
USUARIO = os.environ["CRM_USUARIO"]
SENHA = os.environ["CRM_SENHA"]
CRM_URL = "https://www.rioorla.com.br/crm/p.php"
HOJE = datetime.now().strftime("%d/%m/%Y")
TEXTO_ATUALIZACAO = f"<p>Atualizado em {HOJE}.</p>"
VIVAREAL_VALUE = "9"

# === CONFIGURACOES CANAL PRO ===
CANALPRO_EMAIL = os.environ["CANALPRO_EMAIL"]
CANALPRO_SENHA = os.environ["CANALPRO_SENHA"]
GMAIL_CREDENTIALS_FILE = "gmail_credentials.json"
GMAIL_TOKEN_FILE       = "gmail_token.json"
GMAIL_SCOPES           = ["https://www.googleapis.com/auth/gmail.readonly"]
CANAL_PRO_URL_LOGIN    = "https://canalpro.grupozap.com/login"
CANAL_PRO_URL_LISTINGS = "https://canalpro.grupozap.com/ZAP_OLX/0/listings"
VERIFICACAO_INTERVALO_SEGUNDOS = 1800  # 30 minutos entre verificações
VERIFICACAO_TIMEOUT_SEGUNDOS   = 8 * 3600  # timeout máximo de 8 horas
# Aliases para compatibilidade
CANALPRO_LOGIN_URL = CANAL_PRO_URL_LOGIN
CANALPRO_LISTINGS_BASE_URL = CANAL_PRO_URL_LISTINGS
POLLING_INTERVAL_SECONDS = VERIFICACAO_INTERVALO_SEGUNDOS
MAX_WAIT_SECONDS = VERIFICACAO_TIMEOUT_SEGUNDOS

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
MODO_HEADLESS = os.getenv("MODO_HEADLESS", "false").lower() == "true"
DRY_RUN       = os.getenv("DRY_RUN",  "false").lower() == "true"
SAFE_MODE     = os.getenv("SAFE_MODE", "false").lower() == "true"

CHECKPOINT_DIR = "state"
os.makedirs(CHECKPOINT_DIR, exist_ok=True)

# === CONFIGURACOES PROXY WEBSHARE (Brasil) ===
PROXY_ATIVO  = os.getenv("PROXY_ATIVO", "false").lower() == "true"
PROXY_HOST   = "p.webshare.io"
PROXY_PORTA  = "80"
PROXY_USUARIO = os.getenv("PROXY_USUARIO", "jecuapfw-br-1")
PROXY_SENHA  = os.getenv("PROXY_SENHA", "8a7gx6ckzexa")

# Inicializados dentro de main() após wait_until_10am()
driver = None
wait = None
actions = None


# =============================================================================
# UTILITÁRIOS GERAIS
# =============================================================================

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


def close_any_open_modal():
    try:
        for _ in range(3):
            modals = driver.find_elements(By.CSS_SELECTOR, ".modal-dialog, .modal-content")
            visible_modals = [m for m in modals if m.is_displayed()]
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
                driver.execute_script(
                    """
                    document.querySelectorAll('.modal').forEach(m => {
                        m.style.display = 'none';
                        m.classList.remove('in');
                    });
                    document.body.classList.remove('modal-open');
                    document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
                    """
                )
                time.sleep(1)

        print("🧹 Modais fechados/limpos.")
    except Exception as exc:
        print(f"⚠️ Falha ao fechar modais: {type(exc).__name__} | {repr(exc)}")


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


def open_divulgacao_tab():
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
                    "input[data-tipo='portaispagos'][data-portal-check='1'][value='9']"
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


def get_vivareal_checkbox_parts():
    input_el = wait.until(
        EC.presence_of_element_located(
            (
                By.CSS_SELECTOR,
                "input[data-tipo='portaispagos'][data-portal-check='1'][value='9']"
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


def is_vivareal_checked():
    _, wrapper, _ = get_vivareal_checkbox_parts()
    wrapper_class = wrapper.get_attribute("class") or ""
    return "checked" in wrapper_class


def set_vivareal_checked(checked):
    _, wrapper, helper = get_vivareal_checkbox_parts()
    atual = "checked" in ((wrapper.get_attribute("class") or ""))

    if atual == checked:
        print(f"ℹ️ VivaReal já está {'marcado' if checked else 'desmarcado'}.")
        return

    safe_click(helper)
    time.sleep(0.8)

    _, wrapper, helper = get_vivareal_checkbox_parts()
    novo = "checked" in ((wrapper.get_attribute("class") or ""))

    if novo != checked:
        driver.execute_script("arguments[0].click();", helper)
        time.sleep(0.8)

    _, wrapper, _ = get_vivareal_checkbox_parts()
    final = "checked" in ((wrapper.get_attribute("class") or ""))

    if final != checked:
        raise Exception(f"Falha ao alterar VivaReal para checked={checked}")

    print(f"{'✅' if checked else '🚫'} VivaReal {'marcado' if checked else 'desmarcado'}.")


def get_vivareal_category_label(value):
    return CATEGORIAS_VIVAREAL.get(str(value), "Simples")


def get_vivareal_category_value():
    try:
        select = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#destaque9")))
        value = driver.execute_script("return arguments[0].value;", select) or "0"

        label = driver.execute_script(
            """
            const sel = arguments[0];
            const opt = Array.from(sel.options).find(o => o.value === sel.value);
            return opt ? opt.textContent.trim() : '';
            """,
            select,
        ) or get_vivareal_category_label(value)

        print(f"📌 Categoria VivaReal original: {label} ({value})")
        return value, label

    except Exception as exc:
        print(f"⚠️ Não consegui capturar categoria VivaReal. Usando Simples (0). Erro: {type(exc).__name__} | {repr(exc)}")
        debug_modal_state("erro_get_categoria")
        return "0", "Simples"


def set_vivareal_category_value(value):
    normalized = str(value) if str(value) in CATEGORIAS_VIVAREAL else "0"
    select = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#destaque9")))
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
    wait.until(EC.element_to_be_clickable((By.XPATH, "//select[@data-input='idportal']/option[@value='9']"))).click()
    print("✅ Filtro 'Divulgação em Portais - VivaReal' aplicado.")
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


def go_to_integracoes_parceiros_and_update_vivareal():
    expand_menu_if_needed()

    try:
        a_integracoes = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[.//i[contains(@class,'fa-plug')] and contains(normalize-space(.),'Integrações')]",
                )
            )
        )
        safe_click(a_integracoes)
        time.sleep(1)
        print("✅ Menu: Integrações clicado.")
    except Exception as exc:
        print(f"⚠️ Não consegui clicar em Integrações pelo menu: {type(exc).__name__} | {repr(exc)}")
        try:
            driver.get("https://www.rioorla.com.br/crm/po.php")
            time.sleep(2)
            print("✅ Fallback: aberto po.php diretamente.")
        except Exception as exc_2:
            print(f"⛔ Falha no fallback po.php: {type(exc_2).__name__} | {repr(exc_2)}")
            return

    try:
        a_parceiros = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[contains(@href,'po.php') and .//i[contains(@class,'fa-handshake')] and contains(normalize-space(.),'Parceiros')]",
                )
            )
        )
        safe_click(a_parceiros)
        time.sleep(2)
        print("✅ Menu: Parceiros clicado (po.php).")
    except Exception as exc:
        print(f"⚠️ Não consegui clicar em Parceiros pelo menu: {type(exc).__name__} | {repr(exc)}")
        try:
            driver.get("https://www.rioorla.com.br/crm/po.php")
            time.sleep(2)
            print("✅ Fallback: aberto po.php diretamente.")
        except Exception as exc_2:
            print(f"⛔ Falha no fallback po.php: {type(exc_2).__name__} | {repr(exc_2)}")
            return

    try:
        btn_atualizar_vivareal = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[contains(@class,'btn-update-portal') "
                    "and contains(@onclick,'updatePortais') "
                    "and (contains(@onclick,'VivaReal') or contains(@onclick,'\"id\":\"9\"') "
                    "or contains(@onclick,\"'id':'9'\") or contains(@onclick,'id&quot;:&quot;9'))]",
                )
            )
        )
        safe_click(btn_atualizar_vivareal)
        print("🚀 Cliquei em Atualizar do VivaReal (id 9).")
        time.sleep(5)
        print("✅ Atualização do VivaReal disparada.")
    except Exception as exc:
        print(f"⛔ Não consegui clicar em Atualizar do VivaReal: {type(exc).__name__} | {repr(exc)}")


# =============================================================================
# CHECKPOINT / ROLLBACK
# =============================================================================

_CHECKPOINT_PATH = None   # preenchido em _checkpoint_criar()

def _checkpoint_criar(timestamp_str):
    """Cria o arquivo de checkpoint no início da Parte 1."""
    global _CHECKPOINT_PATH
    fname = f"checkpoint_{timestamp_str}.json"
    _CHECKPOINT_PATH = os.path.join(CHECKPOINT_DIR, fname)
    data = {
        "timestamp": timestamp_str,
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
            from atualizacao_zap import _process_single_item_parte2  # self-import seguro
        except Exception:
            pass
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
        "instrucao": "Remarcar manualmente o VivaReal para os imóveis abaixo no CRM.",
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
    codigos_ja_salvos = set()

    pagina = 1
    while True:
        try:
            wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")))
        except TimeoutException:
            print(f"✅ Página {pagina} sem imóveis para processar.")

        while True:
            buttons = driver.find_elements(By.XPATH, "//button[contains(@onclick,'mdImovelUpdate')]")
            if not buttons:
                print("✅ Nenhum imóvel restante na lista filtrada desta página.")
                break

            print(f"📌 Imóveis restantes na lista filtrada: {len(buttons)}")

            if not is_session_alive():
                raise InvalidSessionIdException("Sessão do navegador foi encerrada.")

            try:
                safe_click(buttons[0])
                wait.until(EC.visibility_of_element_located((By.ID, "titulo-input")))

                update_description_text()
                swap_7th_with_8th_photo()

                if not open_divulgacao_tab():
                    raise Exception("Não abriu Divulgação")

                categoria_value, categoria_nome = get_vivareal_category_value()
                set_vivareal_checked(False)

                codigo = get_property_code_from_modal()
                if not codigo:
                    raise Exception("Código do imóvel vazio")

                if codigo not in codigos_ja_salvos:
                    imoveis_processados.append(
                        {
                            "codigo": codigo,
                            "categoria_vivareal": categoria_value,
                            "categoria_nome": categoria_nome,
                        }
                    )
                    codigos_ja_salvos.add(codigo)

                save_property()
                _checkpoint_registrar_desmarcado(codigo, categoria_value, categoria_nome)
                print("💾 Imóvel salvo na Parte 1.")
                time.sleep(1)

            except Exception as exc:
                print(f"⚠️ Erro na Parte 1: {type(exc).__name__} | {repr(exc)}")
                debug_modal_state("erro_parte1")
                close_any_open_modal()
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
    generic_texts = {"aceitar", "aceitar todos", "accept", "ok", "concordo", "entendi", "salvar"}
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


def _canal_pro_login():
    """
    Abre nova aba, faz login no Canal Pro e retorna o handle da aba do CRM.
    Campos usam type='text' e são identificados pelo atributo name.
    """
    aba_crm = driver.current_window_handle
    print("🔐 Abrindo nova aba para o Canal Pro...")
    driver.execute_script("window.open('');")
    driver.switch_to.window(driver.window_handles[-1])

    # PASSO 1 — Navegar para login
    print("🔐 Navegando para a página de login do Canal Pro...")
    driver.get(CANAL_PRO_URL_LOGIN)
    time.sleep(2)

    # PASSO 2 — Tratar pop-up de cookies
    _canal_pro_handle_cookie_popup()

    # Remove overlays residuais do banner
    driver.execute_script(
        "document.querySelectorAll('[class*=\"adopt-c-\"], [class*=\"cookie-overlay\"], [class*=\"backdrop\"]')"
        ".forEach(el => { if (el.tagName !== 'BUTTON') el.style.display = 'none'; });"
    )

    def _preencher_campo(campo, valor, nome):
        try:
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

    try:
        # PASSO 3 — Localizar campo e-mail (Canal Pro usa type="text", não type="email")
        print("📝 Localizando campo de e-mail...")
        email_field = None
        for locator in [
            (By.CSS_SELECTOR, "input[name='email']"),
            (By.XPATH, "//input[@placeholder='Digite seu e-mail']"),
            (By.CSS_SELECTOR, "input.l-input__item[type='text']"),
        ]:
            try:
                email_field = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located(locator)
                )
                break
            except Exception:
                pass
        if not email_field:
            raise Exception("Campo de e-mail não encontrado na página de login do Canal Pro.")

        # PASSO 4 — Localizar campo senha (Canal Pro usa type="text", não type="password")
        print("📝 Localizando campo de senha...")
        senha_field = None
        for locator in [
            (By.CSS_SELECTOR, "input[name='password']"),
            (By.XPATH, "//input[contains(@placeholder, 'Digite sua senha')]"),
        ]:
            try:
                senha_field = driver.find_element(*locator)
                break
            except Exception:
                pass
        if not senha_field:
            raise Exception("Campo de senha não encontrado na página de login do Canal Pro.")

        # PASSO 5 — Preencher campos com fallback JS e validação
        print(f"📝 Preenchendo e-mail: {CANALPRO_EMAIL}")
        _preencher_campo(email_field, CANALPRO_EMAIL, "email")

        print("📝 Preenchendo senha: [oculta]")
        _preencher_campo(senha_field, CANALPRO_SENHA, "senha")

        # PASSO 6 — Clicar em "Entrar"
        print("🖱️ Clicando em 'Entrar'...")
        btn_entrar = None
        for locator in [
            (By.XPATH, "//button[normalize-space(text())='Entrar']"),
            (By.CSS_SELECTOR, "button[type='submit']"),
        ]:
            try:
                btn_entrar = driver.find_element(*locator)
                break
            except Exception:
                pass

        if btn_entrar:
            try:
                safe_click(btn_entrar)
            except Exception:
                driver.execute_script("arguments[0].click();", btn_entrar)
        else:
            senha_field.send_keys(Keys.RETURN)

        # PASSO 7 — Detectar redirecionamento ou tela de 2FA
        print("⏳ Aguardando resposta pós-login...")
        status = _canal_pro_aguardar_pos_login()

        if status == "2fa":
            print("🔐 Detectada tela de 2FA (verificação em duas etapas).")
            _canal_pro_handle_2fa()
        elif status == "ok":
            pass  # login direto, sem 2FA
        else:
            raise Exception("Login Canal Pro: timeout ou estado inesperado pós-submit.")

        # Confirmação final
        current = driver.current_url
        if "/login" in current:
            raise Exception(f"Login no Canal Pro falhou — URL ainda em /login. URL atual: {current}")

        time.sleep(2)
        _canal_pro_handle_cookie_popup()
        print(f"✅ Login no Canal Pro realizado. URL: {driver.current_url}")
        return aba_crm

    except Exception as exc:
        print(f"⛔ Falha no login do Canal Pro: {type(exc).__name__} | {repr(exc)}")
        raise


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
    raise Exception("2FA: não foi possível clicar em 'Verificar código' ou aguardar redirecionamento.")


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
        raise Exception("2FA: campos de código não encontrados.")

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
    timestamp_inicio = datetime.now()  # marcos para filtrar e-mails anteriores

    print("📧 Autenticando Gmail API...")
    try:
        gmail_service = _gmail_autenticar()
        print("✅ Gmail API autenticado.")
    except Exception as exc:
        raise Exception(f"Falha ao autenticar Gmail API: {repr(exc)}")

    TIMEOUT_SEGUNDOS   = 180
    INTERVALO_SEGUNDOS = 10
    inicio = time.time()
    tentativa = 0

    while time.time() - inicio < TIMEOUT_SEGUNDOS:
        tentativa += 1
        restante = int(TIMEOUT_SEGUNDOS - (time.time() - inicio))
        print(f"   🔍 Tentativa #{tentativa} — buscando código 2FA... ({restante}s restantes)")

        janela = int(time.time() - inicio) + 30
        codigo = _gmail_buscar_codigo_2fa(
            gmail_service,
            janela_segundos=max(janela, 60),
            timestamp_inicio=timestamp_inicio,
        )

        if codigo and len(codigo) == 6 and codigo.isdigit():
            print(f"✅ Código 2FA obtido automaticamente: {codigo}")
            _canal_pro_preencher_codigo_2fa(codigo)
            return

        time.sleep(INTERVALO_SEGUNDOS)

    raise Exception("ERROR_2FA: código não encontrado no Gmail após 180s")


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


def _canal_pro_collect_all_active_codes():
    """
    Varre todas as páginas de anúncios do Canal Pro e retorna um set com
    todos os códigos ativos. Retorna None apenas se MAIS DE UMA página
    diferente retornar 0 sem confirmação de lista vazia (proteção contra
    falso positivo por renderização lenta do React).
    Anúncios com badge 'Bloqueado' ainda constam na listagem e são ATIVOS.
    """
    all_codes = set()
    page = 1
    paginas_invalidas = 0
    MAX_TENTATIVAS_PAGINA = 3
    wait_cards = WebDriverWait(driver, 15)

    def _lista_vazia_confirmada():
        try:
            body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
            if any(t in body_text for t in ["nenhum anúncio", "0 anúncios", "nenhum resultado", "sem anúncios"]):
                return True
            driver.find_element(By.XPATH, "//*[contains(normalize-space(.),'de 0')]")
            return True
        except Exception:
            return False

    def _coletar_codigos_pagina():
        elements = driver.find_elements(By.CSS_SELECTOR, "span.card-content__tag")
        codes = set()
        for el in elements:
            try:
                code = (el.text or "").strip()
                if code.isdigit():
                    codes.add(code)
            except Exception:
                pass
        return codes

    while True:
        # Aguarda cards ou mensagem de lista vazia
        try:
            wait_cards.until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "span.card-content__tag")) > 0
                or any(
                    t in (d.find_element(By.TAG_NAME, "body").text or "").lower()
                    for t in ["nenhum anúncio", "0 anúncios", "nenhum resultado", "sem anúncios"]
                )
            )
        except Exception:
            time.sleep(3)

        # Tentativas por página para lidar com renderização lenta do React
        page_codes = set()
        for tentativa_pag in range(1, MAX_TENTATIVAS_PAGINA + 1):
            page_codes = _coletar_codigos_pagina()

            if page_codes:
                break  # sucesso

            if _lista_vazia_confirmada():
                break  # lista realmente vazia, aceitar 0

            if tentativa_pag < MAX_TENTATIVAS_PAGINA:
                print(f"   ⚠️ Página {page} retornou 0 códigos (tentativa {tentativa_pag}/{MAX_TENTATIVAS_PAGINA}). Aguardando 3s...")
                time.sleep(3)
                driver.execute_script("window.scrollTo(0, 300);")
                time.sleep(1)
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(1)
            else:
                print(f"   ⚠️ Página {page} retornou 0 códigos após {MAX_TENTATIVAS_PAGINA} tentativas. Pulando página.")
                paginas_invalidas += 1

        sorted_codes = sorted(page_codes)
        print(f"   📄 Página {page}: {len(page_codes)} código(s) coletado(s): {sorted_codes}")
        all_codes.update(page_codes)

        # Só invalida o resultado inteiro se mais de uma página diferente falhou
        if paginas_invalidas > 1:
            print("   ⚠️ AVISO: Múltiplas páginas retornaram 0 códigos sem confirmação. Dado inválido.")
            return None

        # Verifica próxima página
        try:
            next_btn = driver.find_element(
                By.CSS_SELECTOR,
                "button[aria-label='Próxima Página'], button.pagination__button--next"
            )
            if next_btn.is_enabled() and "disabled" not in (next_btn.get_attribute("class") or ""):
                safe_click(next_btn)
                time.sleep(2)
                page += 1
            else:
                break
        except Exception:
            break

    return all_codes


def verify_properties_removed_from_zap(imoveis_processados):
    """
    Parte Intermediária: abre o Canal Pro em nova aba e verifica a cada
    VERIFICACAO_INTERVALO_SEGUNDOS se os imóveis da Parte 1 foram removidos
    dos anúncios ativos. Só avança quando TODOS estiverem removidos.
    Timeout máximo: VERIFICACAO_TIMEOUT_SEGUNDOS.
    """
    # Fallback: tenta carregar do JSON se lista vier vazia
    if not imoveis_processados:
        try:
            with open("imoveis_parte1.json", encoding="utf-8") as f:
                data = json.load(f)
                imoveis_processados = data.get("imoveis", [])
            print(f"ℹ️ Lista carregada do imoveis_parte1.json ({len(imoveis_processados)} imóvel(is)).")
        except Exception:
            print("ℹ️ Nenhum imóvel na lista e imoveis_parte1.json não encontrado — verificação ignorada.")
            return

    codigos_alvo = {str(item["codigo"]).strip() for item in imoveis_processados}
    print(f"\n🔍 Parte Intermediária: monitorando remoção de {len(codigos_alvo)} imóvel(is) no ZAP Imóveis...")
    print(f"   Códigos aguardados: {sorted(codigos_alvo)}")
    print(f"   Intervalo entre verificações: {VERIFICACAO_INTERVALO_SEGUNDOS // 60} minuto(s)\n")

    aba_crm = _canal_pro_login()
    _canal_pro_navigate_to_listings()

    inicio = time.time()
    tentativa = 1

    while True:
        # Verifica timeout
        if time.time() - inicio > VERIFICACAO_TIMEOUT_SEGUNDOS:
            print(f"⛔ TIMEOUT: imóveis não foram removidos do ZAP em "
                  f"{VERIFICACAO_TIMEOUT_SEGUNDOS // 3600} hora(s). Encerrando.")
            driver.close()
            driver.switch_to.window(aba_crm)
            raise TimeoutError(f"Timeout de {VERIFICACAO_TIMEOUT_SEGUNDOS // 3600}h na verificação do Canal Pro.")

        horario = datetime.now().strftime("%H:%M:%S")
        print(f"🔍 [{horario}] Verificação #{tentativa} — varrendo anúncios no Canal Pro...")

        try:
            ativos = _canal_pro_collect_all_active_codes()
        except Exception as exc:
            print(f"⚠️ Erro ao coletar códigos: {type(exc).__name__} | {repr(exc)}")
            ativos = None

        if ativos is None:
            print(f"   ⚠️ Resultado inválido — aguardando {VERIFICACAO_INTERVALO_SEGUNDOS // 60} min antes de tentar novamente.")
            time.sleep(VERIFICACAO_INTERVALO_SEGUNDOS)
            tentativa += 1
            _canal_pro_navigate_to_listings()
            continue

        print(f"   📊 Total de códigos ativos no Canal Pro: {len(ativos)}")

        ainda_ativos = codigos_alvo & ativos
        ja_removidos = codigos_alvo - ativos

        if ja_removidos:
            print(f"   ✅ Já removidos do ZAP: {sorted(ja_removidos)}")

        if not ainda_ativos:
            print("✅ TODOS os imóveis confirmados como removidos do ZAP Imóveis!")
            print("🔒 Fechando aba do Canal Pro e retornando ao CRM...\n")
            driver.close()
            driver.switch_to.window(aba_crm)
            return

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






# =============================================================================
# PARTE 2 — REMARCAR VIVAREAL
# =============================================================================

def _process_single_item_parte2(item):
    codigo = (item.get("codigo") or "").strip()
    categoria_value = str(item.get("categoria_vivareal", "0")).strip() or "0"
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

    set_vivareal_checked(True)
    set_vivareal_category_value(categoria_value)
    save_property()
    print(f"💾 Parte 2 concluída para {codigo}: VivaReal marcado como {categoria_nome} ({categoria_value}).")
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
        print("⛔ ATENÇÃO: os seguintes imóveis não foram restaurados no VivaReal:")
        for item in falhas_parte2:
            print(f"- Código {item['codigo']} | Categoria {item['categoria_nome']} ({item['categoria_vivareal']})")

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
        print("   ⚠️  AÇÃO MANUAL NECESSÁRIA: remarcar VivaReal para os códigos abaixo:")
        for item in pendentes:
            print(f"      → {item['codigo']} | {item.get('categoria_nome','?')} ({item.get('categoria_vivareal','?')})")
        _checkpoint_fechar("ERROR_AFTER_MUTATION_ROLLBACK_PENDING")
    else:
        print(f"✅ Rollback concluído: {len(revertidos)} imóvel(is) restaurados.")
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

def wait_until_10am():
    """Aguarda até as 23:00 h do dia atual (ou do próximo dia, se já passou)."""
    now = datetime.now()
    target = now.replace(hour=23, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    delta = (target - now).total_seconds()
    if delta > 0:
        print(f"⏰ Aguardando até {target.strftime('%d/%m/%Y %H:%M:%S')} para iniciar...")
        time.sleep(delta)
    print("🕙 23:00 — iniciando execução.")


# =============================================================================
# MAIN
# =============================================================================

def main():
    global driver, wait, actions

    em_nuvem = os.getenv("CI", "") == "true"
    teste_local = os.getenv("TEST_MODE", "") == "true"

    # Localmente aguarda as 10h; em nuvem ou modo teste, inicia imediatamente
    if not em_nuvem and not teste_local:
        wait_until_10am()
    elif teste_local:
        print("🧪 MODO TESTE: pulando espera das 23h.")

    options = Options()
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-gpu")

    if em_nuvem or MODO_HEADLESS:
        # Modo headless para rodar em servidor Linux sem interface gráfica
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")

    chrome_service = Service(ChromeDriverManager().install())

    usando_headless = em_nuvem or MODO_HEADLESS
    if PROXY_ATIVO:
        print(f"🌐 Proxy ativo: {PROXY_HOST}:{PROXY_PORTA} (Brasil)")
        if usando_headless:
            # Extensões Chrome são incompatíveis com --headless em ambientes sem display.
            # Em modo headless usamos apenas --proxy-server; o IP da VPS está autorizado
            # no WebShare (sem auth), por isso a conexão é aceita.
            options.add_argument(f"--proxy-server=http://{PROXY_HOST}:{PROXY_PORTA}")
            print("   ℹ️ Headless: proxy sem extensão (IP autorizado no WebShare).")
        else:
            # Modo normal (PC local): extensão injeta credenciais para roteamento BR
            options.add_argument(f"--proxy-server=http://{PROXY_HOST}:{PROXY_PORTA}")
            ext_path = _criar_extensao_proxy_auth(PROXY_HOST, PROXY_PORTA, PROXY_USUARIO, PROXY_SENHA)
            options.add_extension(ext_path)

    # Logging do startup do navegador
    print(f"🖥️  Iniciando Chrome (headless={usando_headless}, proxy={PROXY_ATIVO})...")
    try:
        driver = webdriver.Chrome(service=chrome_service, options=options)
        v = driver.capabilities.get("browserVersion", "?")
        cdv = driver.capabilities.get("chrome", {}).get("chromedriverVersion", "?")
        print(f"   Chrome {v} | ChromeDriver {str(cdv)[:30]} | OK")
    except WebDriverException as exc:
        print(f"⛔ ERROR_BROWSER_STARTUP: {type(exc).__name__} | {repr(exc)[:300]}")
        raise
    wait = WebDriverWait(driver, 30)
    actions = ActionChains(driver)

    ts_str            = datetime.now().strftime("%Y%m%d_%H%M")
    inicio_execucao   = datetime.now()
    status_final      = "ERROR_UNKNOWN"
    imoveis_processados = []
    restaurados_parte2  = []
    falhas_parte2       = []
    arquivo_rollback    = None

    if DRY_RUN:
        print("🔍 DRY_RUN ATIVO — nenhuma alteração será feita no CRM.")

    try:
        # ── PRÉ-EXECUÇÃO: valida diretório de logs/checkpoints ────────────────
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        os.makedirs("logs", exist_ok=True)

        # --- LOGIN CRM ---
        driver.get(CRM_URL)
        wait.until(EC.visibility_of_element_located((By.NAME, "usuario"))).send_keys(USUARIO)
        driver.find_element(By.NAME, "senha").send_keys(SENHA + Keys.RETURN)
        time.sleep(5)
        print("✅ Login realizado.")

        if MODO_PULAR_PARTE_1:
            # =====================================================================
            # MODO TESTE: pula Parte 1, retoma da Parte Intermediária
            # =====================================================================
            print("\n⏭️ MODO TESTE: pulando Parte 1 (já executada anteriormente).")
            print("   Lendo imoveis_parte1.json para retomar Parte Intermediária...")

            if not os.path.exists("imoveis_parte1.json"):
                raise Exception(
                    "imoveis_parte1.json não encontrado. "
                    "Não é possível pular a Parte 1 sem esse arquivo."
                )

            with open("imoveis_parte1.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                imoveis_processados = data.get("imoveis", [])

            print(f"📦 {len(imoveis_processados)} imóvel(is) carregados do JSON.")

        else:
            # =====================================================================
            # FLUXO NORMAL: executa Parte 1
            # =====================================================================
            if not go_to_imoveis_page_fresh():
                status_final = "ERROR_BEFORE_MUTATION"
                raise Exception("Não foi possível abrir Imóveis para iniciar a Parte 1.")

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

            print("\n🚧 ===== PARTE 1: desmarcando VivaReal =====")
            imoveis_processados = process_part_1_collect_and_disable_vivareal()
            print(f"📦 Total de imóveis salvos para a Parte 2: {len(imoveis_processados)}")

            with open("imoveis_parte1.json", "w", encoding="utf-8") as f:
                json.dump(
                    {"timestamp": datetime.now().isoformat(), "imoveis": imoveis_processados},
                    f, ensure_ascii=False, indent=2
                )
            print("💾 imoveis_parte1.json salvo.")

            print("🚀 Atualizando VivaReal após Parte 1...")
            go_to_integracoes_parceiros_and_update_vivareal()

        # =====================================================================
        # PARTE INTERMEDIÁRIA
        # =====================================================================
        print("\n🔍 ===== PARTE INTERMEDIÁRIA: verificando remoção no ZAP Imóveis =====")
        verify_properties_removed_from_zap(imoveis_processados)

        # =====================================================================
        # PARTE 2: remarcar VivaReal
        # =====================================================================
        print("\n🚧 ===== PARTE 2: restaurando VivaReal =====")
        restaurados_parte2, falhas_parte2 = process_part_2_restore_vivareal(imoveis_processados)

        print("🚀 Atualizando VivaReal após Parte 2...")
        go_to_integracoes_parceiros_and_update_vivareal()

        if falhas_parte2:
            arquivo_rollback = _gerar_arquivo_rollback_pendente(falhas_parte2, ts_str)
            status_final = "ERROR_AFTER_MUTATION_ROLLBACK_PENDING"
        else:
            status_final = "SUCCESS"
            _checkpoint_fechar("SUCCESS")

    except (InvalidSessionIdException, WebDriverException) as exc:
        cod = "ERROR_BROWSER_STARTUP" if "ERROR_BROWSER_STARTUP" in repr(exc) else "ERROR_BROWSER"
        print(f"\n⛔ {cod}: {type(exc).__name__} | {repr(exc)[:200]}")
        status_final = cod
        _tentar_rollback_se_necessario(imoveis_processados, ts_str)

    except TimeoutError as exc:
        print(f"\n⛔ {exc}")
        status_final = "ERROR_TIMEOUT"
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
        _tentar_rollback_se_necessario(imoveis_processados, ts_str)

    finally:
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
        if driver and (em_nuvem or os.getenv("FECHAR_BROWSER", "") == "1"):
            driver.quit()


if __name__ == "__main__":
    main()
