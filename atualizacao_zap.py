import json
import os
from datetime import datetime, timedelta
import time

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
CANAL_PRO_URL_LOGIN    = "https://canalpro.grupozap.com/login"
CANAL_PRO_URL_LISTINGS = "https://canalpro.grupozap.com/ZAP_OLX/0/listings"
VERIFICACAO_INTERVALO_SEGUNDOS = 600   # 10 minutos entre verificações
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
MODO_PULAR_PARTE_1 = True

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
    Resistente a overlays de cookies e interceptação de elementos.
    """
    aba_crm = driver.current_window_handle
    print("🔐 Abrindo nova aba para o Canal Pro...")
    driver.execute_script("window.open('');")
    driver.switch_to.window(driver.window_handles[-1])

    print("🔐 Fazendo login no Canal Pro...")
    driver.get(CANAL_PRO_URL_LOGIN)
    time.sleep(2)  # Tempo para o banner de cookies aparecer

    _canal_pro_handle_cookie_popup()

    # Remove overlays que possam bloquear interação com o formulário
    driver.execute_script("""
        document.querySelectorAll('[class*="adopt-c-"], [class*="cookie-overlay"], [class*="backdrop"]')
            .forEach(el => { if (el.tagName !== 'BUTTON') el.style.display = 'none'; });
    """)

    try:
        email_field = WebDriverWait(driver, 15).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
        )

        # Preenche e-mail com fallback JS
        try:
            email_field.clear()
            email_field.send_keys(CANALPRO_EMAIL)
        except Exception:
            driver.execute_script("arguments[0].value = arguments[1];", email_field, CANALPRO_EMAIL)
            driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", email_field)
            driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", email_field)

        senha_field = driver.find_element(By.CSS_SELECTOR, "input[type='password']")

        # Preenche senha com fallback JS
        try:
            senha_field.clear()
            senha_field.send_keys(CANALPRO_SENHA)
        except Exception:
            driver.execute_script("arguments[0].value = arguments[1];", senha_field, CANALPRO_SENHA)
            driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", senha_field)
            driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", senha_field)

        btn_entrar = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        try:
            safe_click(btn_entrar)
        except Exception:
            driver.execute_script("arguments[0].click();", btn_entrar)

        # Aguarda redirecionamento pós-login (timeout 20s)
        try:
            WebDriverWait(driver, 20).until(
                lambda d: "performance/home" in d.current_url or "listings" in d.current_url
            )
        except Exception:
            # Validação pós-login: URL ainda em /login?
            current = driver.current_url
            if "/login" in current:
                html = driver.execute_script(
                    "return document.body ? document.body.innerHTML.slice(0, 2000) : '';"
                )
                print(f"🧪 HTML parcial da página de login:\n{html[:1000]}")
                raise Exception(f"Login no Canal Pro falhou — URL ainda em /login após 20s. URL atual: {current}")

        time.sleep(2)
        _canal_pro_handle_cookie_popup()
        print("✅ Login no Canal Pro realizado com sucesso.")
        return aba_crm

    except Exception as exc:
        print(f"⛔ Falha no login do Canal Pro: {type(exc).__name__} | {repr(exc)}")
        raise


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
    todos os códigos ativos. Retorna None se o resultado for inválido
    (0 elementos sem confirmação de lista vazia).
    Anúncios com badge 'Bloqueado' ainda constam na listagem e são ATIVOS.
    """
    all_codes = set()
    page = 1
    wait_cards = WebDriverWait(driver, 15)

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

        elements = driver.find_elements(By.CSS_SELECTOR, "span.card-content__tag")
        page_codes = set()
        for el in elements:
            try:
                code = (el.text or "").strip()
                if code.isdigit():
                    page_codes.add(code)
            except Exception:
                pass

        # Validação anti-falso-positivo
        if not page_codes:
            body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
            lista_vazia = any(t in body_text for t in [
                "nenhum anúncio", "0 anúncios", "nenhum resultado", "sem anúncios"
            ])
            # Verifica contador "0 - 0 de 0" ou similar
            try:
                contador = driver.find_element(
                    By.XPATH,
                    "//*[contains(normalize-space(.),'de 0') or normalize-space(.)='0']"
                )
                lista_vazia = lista_vazia or bool(contador)
            except Exception:
                pass

            if not lista_vazia:
                print(f"   ⚠️ AVISO: Página {page} retornou 0 códigos sem confirmação de lista vazia. Dado inválido.")
                return None

        sorted_codes = sorted(page_codes)
        print(f"   📄 Página {page}: {len(page_codes)} código(s) coletado(s): {sorted_codes}")
        all_codes.update(page_codes)

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
# AGENDAMENTO
# =============================================================================

def wait_until_10am():
    """Aguarda até as 10:00 h do dia atual (ou do próximo dia, se já passou)."""
    now = datetime.now()
    target = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    delta = (target - now).total_seconds()
    if delta > 0:
        print(f"⏰ Aguardando até {target.strftime('%d/%m/%Y %H:%M:%S')} para iniciar a Parte 1...")
        time.sleep(delta)
    print("🕙 10:00 — iniciando Parte 1.")


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
        print("🧪 MODO TESTE: pulando espera das 10h.")

    options = Options()
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-gpu")

    if em_nuvem:
        # Modo headless para rodar em servidor Linux sem interface gráfica
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    wait = WebDriverWait(driver, 30)
    actions = ActionChains(driver)

    try:
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
                raise Exception("Não foi possível abrir Imóveis para iniciar a Parte 1.")

            apply_initial_filters()

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
        # PARTE INTERMEDIÁRIA: verifica no Canal Pro se os imóveis foram
        #                      removidos do ZAP Imóveis
        # =====================================================================
        print("\n🔍 ===== PARTE INTERMEDIÁRIA: verificando remoção no ZAP Imóveis =====")
        verify_properties_removed_from_zap(imoveis_processados)

        # =====================================================================
        # PARTE 2: reabre por código, remarca VivaReal e restaura categoria
        # =====================================================================
        print("\n🚧 ===== PARTE 2: restaurando VivaReal =====")
        restaurados_parte2, falhas_parte2 = process_part_2_restore_vivareal(imoveis_processados)

        print("🚀 Atualizando VivaReal após Parte 2...")
        go_to_integracoes_parceiros_and_update_vivareal()

        print("\n📊 RESUMO FINAL")
        print(f"Parte 1 - imóveis desmarcados: {len(imoveis_processados)}")
        print(f"Parte 2 - imóveis restaurados: {len(restaurados_parte2)}")
        print(f"Parte 2 - falhas: {len(falhas_parte2)}")
        if falhas_parte2:
            print("⛔ Códigos com falha na Parte 2:")
            for item in falhas_parte2:
                print(f"- {item['codigo']} | {item['categoria_nome']} ({item['categoria_vivareal']})")

    except TimeoutError as exc:
        print(f"\n⛔ {exc}")
    except InvalidSessionIdException:
        print("\n⛔ Sessão do Chrome foi perdida durante a execução.")
    except WebDriverException as exc:
        print(f"\n⛔ Erro do navegador/driver: {type(exc).__name__} | {repr(exc)}")
    except Exception as exc:
        print(f"\n⛔ Erro geral: {type(exc).__name__} | {repr(exc)}")
    finally:
        print("\n✅ Processo concluído.")
        if driver and (em_nuvem or os.getenv("FECHAR_BROWSER", "") == "1"):
            driver.quit()


if __name__ == "__main__":
    main()
