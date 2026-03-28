"""
DF-e Client for SEFAZ integration.

Handles fetching NF-e, CT-e, and NFS-e from SEFAZ DF-e Distribution API.
Adapted from NFSe_WebMonitor/nfse_client.py.
"""

import gzip
import base64
import time
import requests
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime

# SEFAZ rate limit: must wait 1 hour when no new documents
SEFAZ_WAIT_HOURS = 1

# HTTP 429 retry settings
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 30  # 30s, 60s, 120s
MAX_BACKOFF_SECONDS = 300
DEFAULT_429_BLOCK_MINUTES = 60

from brazil_module.services.fiscal.cert_utils import CertificateContext
from brazil_module.services.fiscal.xml_parser import NFXMLParser


def _check_rate_limit(company_settings, document_type):
    """
    Check if fetching is allowed based on SEFAZ rate limits.

    SEFAZ requires waiting 1 hour after receiving an empty response
    (no new documents) before making another request.

    Returns:
        tuple: (allowed: bool, wait_minutes: int, message: str)
    """
    field_map = {
        "NF-e": "last_empty_response_nfe",
        "CT-e": "last_empty_response_cte",
        "NFS-e": "last_empty_response_nfse"
    }

    field = field_map.get(document_type)
    if not field:
        return True, 0, ""

    # Check HTTP 429 block first (applies to all document types)
    fetch_blocked = getattr(company_settings, "fetch_blocked_until", None)
    if fetch_blocked:
        blocked_dt = get_datetime(fetch_blocked)
        now = now_datetime()
        if now < blocked_dt:
            wait_minutes = int((blocked_dt - now).total_seconds() / 60)
            return False, wait_minutes, _(
                "SEFAZ blocked (HTTP 429): must wait {0} more minutes. "
                "Blocked until {1}."
            ).format(wait_minutes, blocked_dt.strftime("%H:%M:%S"))

    last_empty = getattr(company_settings, field, None)
    if not last_empty:
        return True, 0, ""

    last_empty_dt = get_datetime(last_empty)
    wait_until = last_empty_dt + timedelta(hours=SEFAZ_WAIT_HOURS)
    now = now_datetime()

    if now < wait_until:
        wait_minutes = int((wait_until - now).total_seconds() / 60)
        return False, wait_minutes, _(
            "SEFAZ rate limit: must wait {0} more minutes. "
            "Last empty response was at {1}."
        ).format(wait_minutes, last_empty_dt.strftime("%H:%M:%S"))

    return True, 0, ""


def _handle_429_response(response, company_settings):
    """
    Handle HTTP 429 (Too Many Requests) by blocking future fetches.

    Reads Retry-After header if present, otherwise blocks for DEFAULT_429_BLOCK_MINUTES.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            block_seconds = int(retry_after)
        except ValueError:
            block_seconds = DEFAULT_429_BLOCK_MINUTES * 60
    else:
        block_seconds = DEFAULT_429_BLOCK_MINUTES * 60

    block_until = now_datetime() + timedelta(seconds=block_seconds)
    block_minutes = block_seconds // 60

    frappe.db.set_value(
        "NF Company Settings",
        company_settings.name,
        "fetch_blocked_until",
        block_until,
        update_modified=False,
    )
    frappe.logger().warning(
        f"SEFAZ HTTP 429: Rate limited. Blocking fetches for {block_minutes} minutes "
        f"until {block_until.strftime('%H:%M:%S')}."
    )

    return {
        "status": "rate_limited",
        "fetched": 0,
        "created": 0,
        "skipped": 0,
        "message": _(
            "SEFAZ rate limit (HTTP 429): Too many requests. "
            "Fetching blocked for {0} minutes."
        ).format(block_minutes),
    }


def _request_with_retry(session, method, url, max_retries=MAX_RETRIES, **kwargs):
    """
    Make HTTP request with retry on 429 using exponential backoff.

    Returns the response object. Raises on non-429 HTTP errors.
    """
    for attempt in range(max_retries + 1):
        if method == "get":
            response = session.get(url, **kwargs)
        else:
            response = session.post(url, **kwargs)

        if response.status_code != 429:
            return response

        if attempt < max_retries:
            wait = min(INITIAL_BACKOFF_SECONDS * (2 ** attempt), MAX_BACKOFF_SECONDS)
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = min(int(retry_after), MAX_BACKOFF_SECONDS)
                except ValueError:
                    pass
            frappe.logger().warning(
                f"HTTP 429: Retry {attempt + 1}/{max_retries} after {wait}s"
            )
            time.sleep(wait)

    # All retries exhausted, return the 429 response
    return response


def _update_rate_limit(company_settings, document_type, had_documents):
    """
    Update rate limit tracking after a fetch using direct DB update.

    If no documents were returned, record the time so we know to wait 1 hour.
    If documents were returned, clear the empty response time.
    """
    field_map = {
        "NF-e": "last_empty_response_nfe",
        "CT-e": "last_empty_response_cte",
        "NFS-e": "last_empty_response_nfse"
    }

    field = field_map.get(document_type)
    if not field:
        return

    if had_documents:
        # Clear the empty response time - we got documents
        new_value = None
    else:
        # Record that we got an empty response - must wait 1 hour
        new_value = now_datetime()
        frappe.logger().info(
            f"SEFAZ rate limit: No documents for {document_type}. "
            f"Must wait {SEFAZ_WAIT_HOURS} hour(s) before next fetch."
        )

    # Use direct DB update to avoid document modification conflicts
    frappe.db.set_value(
        "NF Company Settings",
        company_settings.name,
        field,
        new_value,
        update_modified=False
    )
    # Update local attribute for consistency
    setattr(company_settings, field, new_value)


# SEFAZ DF-e Distribution endpoints
SEFAZ_ENDPOINTS = {
    "nfe": {
        "production": "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
        "homologation": "https://hom.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
    },
    "cte": {
        "production": "https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx",
        "homologation": "https://hom.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx"
    },
    "nfse": {
        "production": "https://adn.nfse.gov.br/contribuintes/DFe",
        "homologation": "https://adn.producaorestrita.nfse.gov.br/contribuintes/DFe"
    }
}


def scheduled_fetch():
    """
    Scheduled job to fetch documents from SEFAZ for all enabled companies.
    """
    settings = frappe.get_single("Nota Fiscal Settings")

    if not settings.enabled:
        return

    from brazil_module.fiscal.doctype.nf_company_settings.nf_company_settings import get_all_enabled_companies

    companies = get_all_enabled_companies()

    for company_data in companies:
        try:
            fetch_documents_for_company(company_data["name"])
        except Exception as e:
            frappe.log_error(str(e), f"SEFAZ Fetch Error: {company_data['company']}")


def fetch_documents_for_company(company_settings_name, document_type=None):
    """
    Fetch documents from SEFAZ for a specific company.

    Args:
        company_settings_name: Name of NF Company Settings document
        document_type: Optional specific document type (NF-e, CT-e, NFS-e)

    Returns:
        dict: Fetch results
    """
    from brazil_module.fiscal.doctype.nf_company_settings.nf_company_settings import get_company_settings
    from brazil_module.fiscal.doctype.nf_import_log.nf_import_log import create_import_log

    company_settings = frappe.get_doc("NF Company Settings", company_settings_name)
    settings = frappe.get_single("Nota Fiscal Settings")

    if not company_settings.certificate_valid:
        return {"status": "error", "message": _("Certificate not valid")}

    # Determine which document types to fetch
    doc_types = []

    if document_type:
        doc_types = [document_type]
    else:
        if settings.nfe_enabled:
            doc_types.append("NF-e")
        if settings.cte_enabled:
            doc_types.append("CT-e")
        if settings.nfse_enabled:
            doc_types.append("NFS-e")

    results = {}

    # Get environment for display
    env = _get_sefaz_environment(company_settings, settings)
    env_display = "Produção" if env == "production" else "Homologação"

    for doc_type in doc_types:
        # Check rate limit before fetching
        allowed, wait_minutes, rate_limit_msg = _check_rate_limit(company_settings, doc_type)

        if not allowed:
            frappe.logger().info(f"Skipping {doc_type} fetch: {rate_limit_msg}")
            results[doc_type] = {
                "status": "rate_limited",
                "fetched": 0,
                "created": 0,
                "skipped": 0,
                "message": rate_limit_msg,
                "wait_minutes": wait_minutes
            }
            continue

        log = create_import_log(
            company_settings.company,
            doc_type,
            "SEFAZ"
        )

        try:
            result = _fetch_documents(company_settings, doc_type, settings, log)
            result["environment"] = env_display  # Add environment to result
            results[doc_type] = result

            # Update rate limit tracking (uses direct DB update)
            had_documents = result.get("fetched", 0) > 0
            _update_rate_limit(company_settings, doc_type, had_documents)

            log.mark_completed("Success" if result["created"] > 0 else "Partial")
        except Exception as e:
            log.mark_failed(str(e))
            results[doc_type] = {"status": "error", "message": str(e), "environment": env_display}

    return results


def _get_sefaz_environment(company_settings, global_settings):
    """
    Get SEFAZ environment, prioritizing company-level setting over global.

    Returns:
        str: 'production' or 'homologation'
    """
    # Company-level setting takes priority
    if company_settings.sefaz_environment:
        return company_settings.sefaz_environment.lower()

    # Fall back to global setting
    if global_settings.sefaz_environment:
        return global_settings.sefaz_environment.lower()

    # Default to production
    return "production"


def _fetch_documents(company_settings, document_type, settings, log):
    """
    Internal function to fetch documents of a specific type.
    """
    # Get endpoint - company setting takes priority over global
    env = _get_sefaz_environment(company_settings, settings)
    doc_type_key = document_type.lower().replace("-", "")

    if doc_type_key not in SEFAZ_ENDPOINTS:
        raise ValueError(f"Unknown document type: {document_type}")

    endpoint = SEFAZ_ENDPOINTS[doc_type_key].get(env)

    if not endpoint:
        raise ValueError(f"No endpoint for {document_type} in {env}")

    # Get last NSU
    last_nsu = company_settings.get_last_nsu(document_type)

    # Get decrypted password
    certificate_password = company_settings.get_certificate_password()

    # Use certificate context for automatic cleanup
    with CertificateContext(company_settings.certificate_file, certificate_password) as (cert_path, key_path):
        # For NFS-e (REST API)
        if document_type == "NFS-e":
            return _fetch_nfse_documents(endpoint, cert_path, key_path, last_nsu, company_settings, log)
        else:
            # For NF-e and CT-e (SOAP API)
            return _fetch_dfe_documents(endpoint, cert_path, key_path, last_nsu, document_type, company_settings, log)


def _fetch_nfse_documents(endpoint, cert_path, key_path, last_nsu, company_settings, log):
    """
    Fetch NFS-e documents using REST API.

    Adapted from NFSe_WebMonitor/nfse_client.py
    """
    url = f"{endpoint}/{last_nsu}"

    frappe.logger().info(f"NFS-e Fetch: URL={url}, NSU={last_nsu}")

    session = requests.Session()
    session.cert = (cert_path, key_path)
    session.headers.update({
        "Accept": "application/json"
    })

    try:
        response = _request_with_retry(session, "get", url, timeout=60)
    except requests.exceptions.SSLError as e:
        frappe.logger().error(f"NFS-e Fetch: SSL/Certificate error: {e}")
        return {
            "status": "error",
            "fetched": 0,
            "created": 0,
            "skipped": 0,
            "message": _(
                "SSL/Certificate error when connecting to NFS-e API. "
                "Please verify the certificate is valid and not expired. Error: {0}"
            ).format(str(e)),
            "nsu_used": last_nsu
        }
    except requests.exceptions.ConnectionError as e:
        frappe.logger().error(f"NFS-e Fetch: Connection error: {e}")
        return {
            "status": "error",
            "fetched": 0,
            "created": 0,
            "skipped": 0,
            "message": _("Connection error: NFS-e API may be temporarily unavailable. Error: {0}").format(str(e)),
            "nsu_used": last_nsu
        }

    frappe.logger().info(f"NFS-e Fetch: HTTP Status={response.status_code}")
    frappe.logger().info(f"NFS-e Fetch: Response={response.text[:1000] if response.text else 'empty'}")

    # Handle 429: rate limited after retries exhausted
    if response.status_code == 429:
        return _handle_429_response(response, company_settings)

    # Handle 404: NSU may have expired on SEFAZ side. Retry with NSU=0.
    if response.status_code == 404 and str(last_nsu) != "0":
        frappe.logger().warning(
            f"NFS-e Fetch: Got 404 for NSU={last_nsu}. "
            f"NSU may have expired. Retrying with NSU=0..."
        )
        url = f"{endpoint}/0"
        response = _request_with_retry(session, "get", url, timeout=60)

        frappe.logger().info(f"NFS-e Fetch (retry): HTTP Status={response.status_code}")
        frappe.logger().info(f"NFS-e Fetch (retry): Response={response.text[:1000] if response.text else 'empty'}")

        if response.status_code == 429:
            return _handle_429_response(response, company_settings)

        if response.ok:
            frappe.logger().info(
                f"NFS-e Fetch: NSU reset successful. Old NSU={last_nsu} was stale."
            )
            company_settings.update_last_nsu("NFS-e", "0")

    if response.status_code == 404:
        frappe.logger().error(
            f"NFS-e Fetch: API returning 404. URL: {url}. "
            f"This may indicate: (1) certificate/mTLS issue, "
            f"(2) CNPJ mismatch with certificate, or "
            f"(3) API endpoint change."
        )
        return {
            "status": "error",
            "fetched": 0,
            "created": 0,
            "skipped": 0,
            "message": _(
                "NFS-e API returned 404. Possible causes: "
                "(1) Certificate expired or invalid for mTLS, "
                "(2) Certificate CNPJ does not match company CNPJ, "
                "(3) NFS-e ADN API temporarily unavailable. "
                "Please verify your certificate in NF Company Settings."
            ),
            "nsu_used": last_nsu
        }

    response.raise_for_status()

    data = response.json()

    # Log API response status
    status = data.get("StatusProcessamento", "unknown")
    erros = data.get("Erros", [])
    alertas = data.get("Alertas", [])
    frappe.logger().info(f"NFS-e Fetch: StatusProcessamento={status}")
    if erros:
        frappe.logger().warning(f"NFS-e Fetch: Erros={erros}")
    if alertas:
        frappe.logger().info(f"NFS-e Fetch: Alertas={alertas}")

    documents = data.get("LoteDFe", [])
    frappe.logger().info(f"NFS-e Fetch: Found {len(documents)} documents in LoteDFe")

    # Log document structure for debugging
    if documents:
        sample_doc = documents[0]
        frappe.logger().info(f"NFS-e Fetch: Sample document keys: {list(sample_doc.keys())}")
        frappe.logger().info(f"NFS-e Fetch: Sample NSU value: {sample_doc.get('NSU')} (type: {type(sample_doc.get('NSU')).__name__})")

    created = 0
    skipped = 0
    events_processed = 0

    # Track NSU range for batch update at end
    nsu_values = []

    for doc_data in documents:
        try:
            nsu = doc_data.get("NSU")
            chave = doc_data.get("ChaveAcesso")
            tipo_doc = doc_data.get("TipoDocumento")
            tipo_evento = doc_data.get("TipoEvento")
            xml_b64 = doc_data.get("ArquivoXml")

            # Track NSU for range update
            if nsu:
                nsu_values.append(nsu)

            # Handle events (cancellation, etc.)
            if tipo_doc == "EVENTO":
                _process_evento(chave, tipo_evento, xml_b64)
                events_processed += 1
                continue

            # Decode XML
            xml_content = _decode_xml(xml_b64)

            # Check for duplicates by chave_de_acesso
            if chave and frappe.db.exists("Nota Fiscal", {"chave_de_acesso": chave}):
                # Update origin flag
                existing = frappe.get_value("Nota Fiscal", {"chave_de_acesso": chave}, "name")
                frappe.db.set_value("Nota Fiscal", existing, "origin_sefaz", 1)
                skipped += 1
                frappe.logger().info(f"NFS-e Fetch: Skipped duplicate by chave: {chave}")
                continue

            # Also check by NSU to prevent duplicates if chave is missing
            if nsu and frappe.db.exists("Nota Fiscal", {"nsu": str(nsu), "company": company_settings.company}):
                skipped += 1
                frappe.logger().info(f"NFS-e Fetch: Skipped duplicate by NSU: {nsu}")
                continue

            # Create Nota Fiscal
            _create_nota_fiscal_from_xml(xml_content, "NFS-e", company_settings, chave, nsu)
            created += 1

        except Exception as e:
            frappe.log_error(str(e), f"Error processing NFS-e document")
            log.update_counts(failed=1)

    # Update NSU range once at the end (more efficient)
    if nsu_values:
        log.first_nsu = log.first_nsu or str(min(nsu_values))
        log.last_nsu = str(max(nsu_values))
        log.save(ignore_permissions=True)

    # Update company settings with last NSU
    max_nsu = int(last_nsu or 0)
    nsu_list = []

    if documents:
        # Find the highest NSU from all documents
        for doc in documents:
            doc_nsu = doc.get("NSU")
            frappe.logger().info(f"NFS-e Fetch: Document NSU = {doc_nsu} (type: {type(doc_nsu).__name__})")
            if doc_nsu is not None:
                try:
                    doc_nsu_int = int(doc_nsu)
                    nsu_list.append(doc_nsu_int)
                    if doc_nsu_int > max_nsu:
                        max_nsu = doc_nsu_int
                except (ValueError, TypeError) as e:
                    frappe.logger().warning(f"NFS-e Fetch: Could not parse NSU {doc_nsu}: {e}")

        frappe.logger().info(f"NFS-e Fetch: NSU list = {nsu_list}, max = {max_nsu}")

        if max_nsu > int(last_nsu or 0):
            frappe.logger().info(f"NFS-e Fetch: Updating NSU from {last_nsu} to {max_nsu}")
            company_settings.update_last_nsu("NFS-e", str(max_nsu))
        else:
            frappe.logger().info(f"NFS-e Fetch: NSU not updated (max {max_nsu} <= current {last_nsu})")

    log.update_counts(fetched=len(documents), created=created, skipped=skipped)

    return {
        "status": "success",
        "fetched": len(documents),
        "created": created,
        "skipped": skipped,
        "events": events_processed,
        "sefaz_status": status,
        "nsu_used": last_nsu
    }


def _fetch_dfe_documents(endpoint, cert_path, key_path, last_nsu, document_type, company_settings, log):
    """
    Fetch NF-e or CT-e documents using SOAP DistDFeInt API.

    Uses mTLS (client certificate) authentication. The DistDFeInt operation
    does NOT require XML digital signature - just the SOAP envelope over mTLS.
    """
    import xml.etree.ElementTree as ET

    # Determine SOAP parameters based on document type
    if document_type == "NF-e":
        ws_ns = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"
        schema_ns = "http://www.portalfiscal.inf.br/nfe"
        soap_action = f"{ws_ns}/nfeDistDFeInteresse"
    else:  # CT-e
        ws_ns = "http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe"
        schema_ns = "http://www.portalfiscal.inf.br/cte"
        soap_action = f"{ws_ns}/cteDistDFeInteresse"

    # Build SOAP envelope
    env = company_settings.sefaz_environment or "production"
    tpAmb = "1" if env == "production" else "2"
    cUFAutor = company_settings.uf_code or "35"
    cnpj = (company_settings.cnpj or "").replace(".", "").replace("/", "").replace("-", "")
    ult_nsu = str(last_nsu or "0").zfill(15)

    soap_envelope = _build_dist_dfe_request(tpAmb, cUFAutor, cnpj, ult_nsu, schema_ns)

    frappe.logger().info(
        f"{document_type} SOAP Fetch: endpoint={endpoint}, NSU={ult_nsu}, "
        f"tpAmb={tpAmb}, cUFAutor={cUFAutor}"
    )

    # Send SOAP request with mTLS
    session = requests.Session()
    session.cert = (cert_path, key_path)

    headers = {
        "Content-Type": "application/soap+xml; charset=utf-8",
        "SOAPAction": soap_action,
    }

    try:
        response = _request_with_retry(session, "post", endpoint, data=soap_envelope, headers=headers, timeout=60)
    except requests.exceptions.SSLError as e:
        frappe.logger().error(f"{document_type} SOAP Fetch: SSL error: {e}")
        return {
            "status": "error",
            "fetched": 0, "created": 0, "skipped": 0,
            "message": f"SSL/Certificate error: {e}",
            "nsu_used": last_nsu,
        }
    except requests.exceptions.ConnectionError as e:
        frappe.logger().error(f"{document_type} SOAP Fetch: Connection error: {e}")
        return {
            "status": "error",
            "fetched": 0, "created": 0, "skipped": 0,
            "message": f"Connection error: {e}",
            "nsu_used": last_nsu,
        }

    frappe.logger().info(f"{document_type} SOAP Fetch: HTTP status={response.status_code}")

    # Handle 429: rate limited after retries exhausted
    if response.status_code == 429:
        return _handle_429_response(response, company_settings)

    response.raise_for_status()

    # Parse SOAP response
    result = _parse_dist_dfe_response(response.content, schema_ns)

    cStat = result.get("cStat", "")
    xMotivo = result.get("xMotivo", "")
    documents = result.get("documents", [])
    max_nsu = result.get("maxNSU", "")
    ult_nsu_resp = result.get("ultNSU", "")

    frappe.logger().info(
        f"{document_type} SOAP Fetch: cStat={cStat}, xMotivo={xMotivo}, "
        f"docs={len(documents)}, maxNSU={max_nsu}, ultNSU={ult_nsu_resp}"
    )

    # cStat 137 = documents found, 138 = no new documents
    if cStat == "138":
        return {
            "status": "success",
            "fetched": 0, "created": 0, "skipped": 0,
            "message": xMotivo,
            "nsu_used": last_nsu,
        }

    if cStat not in ("137", "138"):
        return {
            "status": "error",
            "fetched": 0, "created": 0, "skipped": 0,
            "message": f"SEFAZ returned cStat={cStat}: {xMotivo}",
            "nsu_used": last_nsu,
        }

    # Process documents
    created = 0
    skipped = 0
    events_processed = 0
    nsu_values = []

    for doc_data in documents:
        try:
            nsu = doc_data.get("NSU")
            schema = doc_data.get("schema", "")
            xml_content = doc_data.get("xml_content")

            if nsu:
                nsu_values.append(nsu)

            # Check if it's an event (cancellation, etc.)
            if "evento" in schema.lower() or "resEvento" in schema:
                # Extract chave from XML and process as event
                chave = _extract_chave_from_xml(xml_content)
                _process_evento(chave, schema, None)
                events_processed += 1
                continue

            # Extract chave from XML for duplicate check
            chave = _extract_chave_from_xml(xml_content)

            if chave and frappe.db.exists("Nota Fiscal", {"chave_de_acesso": chave}):
                existing = frappe.get_value("Nota Fiscal", {"chave_de_acesso": chave}, "name")
                frappe.db.set_value("Nota Fiscal", existing, "origin_sefaz", 1)
                skipped += 1
                continue

            if nsu and frappe.db.exists("Nota Fiscal", {"nsu": str(nsu), "company": company_settings.company}):
                skipped += 1
                continue

            _create_nota_fiscal_from_xml(xml_content, document_type, company_settings, chave, nsu)
            created += 1

        except Exception as e:
            frappe.log_error(str(e), f"Error processing {document_type} document")
            log.update_counts(failed=1)

    # Update NSU tracking
    if nsu_values:
        log.first_nsu = log.first_nsu or str(min(nsu_values))
        log.last_nsu = str(max(nsu_values))
        log.save(ignore_permissions=True)

    # Update company's last NSU
    if ult_nsu_resp:
        try:
            new_nsu = int(ult_nsu_resp)
            old_nsu = int(last_nsu or 0)
            if new_nsu > old_nsu:
                company_settings.update_last_nsu(document_type, str(new_nsu))
        except (ValueError, TypeError):
            pass

    log.update_counts(fetched=len(documents), created=created, skipped=skipped)

    return {
        "status": "success",
        "fetched": len(documents),
        "created": created,
        "skipped": skipped,
        "events": events_processed,
        "nsu_used": last_nsu,
    }


def _build_dist_dfe_request(tpAmb, cUFAutor, cnpj, ultNSU, schema_ns):
    """
    Build SOAP 1.2 envelope for DistDFeInt (Distribution of DF-e) request.

    Args:
        tpAmb: Environment (1=Production, 2=Homologation)
        cUFAutor: UF IBGE code of the requesting entity
        cnpj: Company CNPJ (14 digits)
        ultNSU: Last NSU received (15-digit zero-padded string)
        schema_ns: XML schema namespace for the document type

    Returns:
        str: SOAP envelope XML string
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body>'
        f'<nfeDistDFeInteresse xmlns="{schema_ns}">'
        f'<distDFeInt xmlns="{schema_ns}">'
        f'<tpAmb>{tpAmb}</tpAmb>'
        f'<cUFAutor>{cUFAutor}</cUFAutor>'
        f'<CNPJ>{cnpj}</CNPJ>'
        f'<distNSU><ultNSU>{ultNSU}</ultNSU></distNSU>'
        '</distDFeInt>'
        '</nfeDistDFeInteresse>'
        '</soap12:Body>'
        '</soap12:Envelope>'
    )


def _parse_dist_dfe_response(response_content, schema_ns):
    """
    Parse SOAP response from DistDFeInt.

    Extracts status code, documents (base64+gzip compressed), and NSU tracking.

    Args:
        response_content: Raw bytes of the SOAP response
        schema_ns: XML schema namespace for the document type

    Returns:
        dict: Parsed response with cStat, xMotivo, documents list, maxNSU, ultNSU
    """
    import xml.etree.ElementTree as ET

    result = {
        "cStat": "",
        "xMotivo": "",
        "documents": [],
        "maxNSU": "",
        "ultNSU": "",
    }

    try:
        root = ET.fromstring(response_content)
    except ET.ParseError as e:
        frappe.log_error(f"Failed to parse SOAP response: {e}", "DFe SOAP Parse Error")
        result["cStat"] = "999"
        result["xMotivo"] = f"XML parse error: {e}"
        return result

    # Search for retDistDFeInt element in any namespace
    ret_element = None
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "retDistDFeInt":
            ret_element = elem
            break

    if ret_element is None:
        result["cStat"] = "999"
        result["xMotivo"] = "retDistDFeInt element not found in response"
        return result

    # Extract status fields by iterating children (namespace-agnostic)
    for child in ret_element:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "cStat":
            result["cStat"] = child.text or ""
        elif tag == "xMotivo":
            result["xMotivo"] = child.text or ""
        elif tag == "maxNSU":
            result["maxNSU"] = child.text or ""
        elif tag == "ultNSU":
            result["ultNSU"] = child.text or ""
        elif tag == "loteDistDFeInt":
            # Process each document in the lot
            for doc_zip in child:
                doc_tag = doc_zip.tag.split("}")[-1] if "}" in doc_zip.tag else doc_zip.tag
                if doc_tag == "docZip":
                    nsu = doc_zip.get("NSU", "")
                    schema = doc_zip.get("schema", "")
                    xml_b64 = doc_zip.text or ""

                    xml_content = _decode_xml(xml_b64) if xml_b64 else None
                    if xml_content:
                        result["documents"].append({
                            "NSU": nsu,
                            "schema": schema,
                            "xml_content": xml_content,
                        })

    return result


def _extract_chave_from_xml(xml_content):
    """
    Extract chave de acesso from a parsed XML document.

    Searches for Id attributes starting with NFe, CTe, or NFS.
    """
    import xml.etree.ElementTree as ET

    if not xml_content:
        return None

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    # Search all elements for Id attribute with known prefixes
    for elem in root.iter():
        id_attr = elem.get("Id", "")
        if id_attr.startswith("NFe"):
            return id_attr[3:]
        if id_attr.startswith("CTe"):
            return id_attr[3:]
        if id_attr.startswith("NFS"):
            return id_attr[3:]

    return None


def _decode_xml(xml_b64):
    """
    Decode base64-encoded gzipped XML.
    """
    if not xml_b64:
        return None

    # Decode base64
    compressed = base64.b64decode(xml_b64)

    # Decompress gzip
    try:
        xml_bytes = gzip.decompress(compressed)
    except gzip.BadGzipFile:
        # Not gzipped, try as plain base64
        xml_bytes = compressed

    return xml_bytes.decode("utf-8")


def _process_evento(chave_acesso, tipo_evento, xml_b64):
    """
    Process an event (cancellation, correction, etc.) for an existing NF.

    Args:
        chave_acesso: Access key of the related NF
        tipo_evento: Event type (e.g., "Cancelamento", "101101")
        xml_b64: Base64-encoded event XML
    """
    if not chave_acesso:
        return

    # Find the related Nota Fiscal
    nf_name = frappe.db.get_value("Nota Fiscal", {"chave_de_acesso": chave_acesso}, "name")

    if not nf_name:
        frappe.logger().warning(f"Event received for unknown NF: {chave_acesso}")
        return

    # Decode event XML for details
    xml_content = _decode_xml(xml_b64) if xml_b64 else None

    # Determine event type and process accordingly
    tipo_evento_lower = (tipo_evento or "").lower()

    # Cancellation event codes/names
    cancellation_indicators = [
        "cancelamento", "cancel", "101101", "101", "e101101"
    ]

    is_cancellation = any(ind in tipo_evento_lower for ind in cancellation_indicators)

    if is_cancellation:
        # Get full NF document to check for linked documents
        nf_doc = frappe.get_doc("Nota Fiscal", nf_name)

        # Check for linked Purchase Invoice
        linked_docs_issues = []
        if nf_doc.purchase_invoice:
            pi_result = _handle_linked_purchase_invoice(nf_doc.purchase_invoice, nf_name)
            if not pi_result["success"]:
                linked_docs_issues.append(pi_result)

        # Mark the NF as cancelled
        frappe.db.set_value(
            "Nota Fiscal",
            nf_name,
            {
                "cancelada": 1,
                "status_sefaz": "Cancelada",
                "processing_status": "Cancelled",
                "data_cancelamento": now_datetime()
            },
            update_modified=True
        )

        frappe.logger().info(f"NF {nf_name} marked as cancelled (event: {tipo_evento})")

        # Store event XML if available
        if xml_content:
            try:
                nf_doc.reload()
                nf_doc.append("eventos", {
                    "tipo_evento": "Cancelamento",
                    "codigo_evento": tipo_evento,
                    "data_evento": now_datetime(),
                    "descricao": "Cancelamento de NFS-e",
                    "xml_evento": xml_content
                })
                nf_doc.save(ignore_permissions=True)
            except Exception as e:
                frappe.logger().warning(f"Could not add event to NF {nf_name}: {e}")

        # Send alert email if there were issues with linked documents
        if linked_docs_issues:
            _send_cancellation_alert(nf_doc, linked_docs_issues)
    else:
        frappe.logger().info(f"Event {tipo_evento} received for NF {nf_name} (not processed)")


def _handle_linked_purchase_invoice(pi_name, nf_name):
    """
    Handle a linked Purchase Invoice when the NF is cancelled.

    Attempts to cancel the Purchase Invoice if possible.

    Args:
        pi_name: Name of the Purchase Invoice
        nf_name: Name of the Nota Fiscal

    Returns:
        dict: Result with success flag and message
    """
    try:
        pi_doc = frappe.get_doc("Purchase Invoice", pi_name)

        # Check if invoice is already cancelled
        if pi_doc.docstatus == 2:
            return {
                "success": True,
                "document_type": "Purchase Invoice",
                "document_name": pi_name,
                "message": "Already cancelled"
            }

        # Check if invoice is submitted
        if pi_doc.docstatus == 1:
            # Try to cancel it
            try:
                pi_doc.flags.ignore_permissions = True
                pi_doc.cancel()
                frappe.logger().info(f"Purchase Invoice {pi_name} cancelled due to NF {nf_name} cancellation")
                return {
                    "success": True,
                    "document_type": "Purchase Invoice",
                    "document_name": pi_name,
                    "message": "Cancelled successfully"
                }
            except Exception as e:
                # Cancellation failed - likely due to linked GL entries, payments, etc.
                frappe.logger().warning(
                    f"Could not cancel Purchase Invoice {pi_name}: {e}"
                )
                return {
                    "success": False,
                    "document_type": "Purchase Invoice",
                    "document_name": pi_name,
                    "message": str(e),
                    "action_required": "Manual cancellation required"
                }
        else:
            # Invoice is in draft - just delete it
            try:
                frappe.delete_doc("Purchase Invoice", pi_name, ignore_permissions=True)
                frappe.logger().info(f"Draft Purchase Invoice {pi_name} deleted due to NF {nf_name} cancellation")
                return {
                    "success": True,
                    "document_type": "Purchase Invoice",
                    "document_name": pi_name,
                    "message": "Draft deleted"
                }
            except Exception as e:
                return {
                    "success": False,
                    "document_type": "Purchase Invoice",
                    "document_name": pi_name,
                    "message": str(e),
                    "action_required": "Manual deletion required"
                }

    except Exception as e:
        frappe.logger().error(f"Error handling linked Purchase Invoice {pi_name}: {e}")
        return {
            "success": False,
            "document_type": "Purchase Invoice",
            "document_name": pi_name,
            "message": str(e),
            "action_required": "Check document status"
        }


def _send_cancellation_alert(nf_doc, issues):
    """
    Send email alert when a cancellation event cannot fully process linked documents.

    Args:
        nf_doc: The Nota Fiscal document
        issues: List of issues with linked documents
    """
    settings = frappe.get_single("Nota Fiscal Settings")

    # Check if alerts are enabled and email is configured
    if not settings.send_cancellation_alerts:
        return

    if not settings.alert_email:
        frappe.logger().warning("Cancellation alert not sent: No alert email configured")
        return

    # Build email content
    subject = _("Action Required: NF Cancellation - {0}").format(nf_doc.name)

    issues_html = ""
    for issue in issues:
        issues_html += f"""
        <tr>
            <td>{issue.get('document_type', '')}</td>
            <td>{issue.get('document_name', '')}</td>
            <td>{issue.get('message', '')}</td>
            <td><strong>{issue.get('action_required', '')}</strong></td>
        </tr>
        """

    message = f"""
    <h3>Nota Fiscal Cancellation Alert</h3>

    <p>A Nota Fiscal was cancelled at SEFAZ but some linked documents could not be cancelled automatically.</p>

    <h4>Nota Fiscal Details:</h4>
    <ul>
        <li><strong>Document:</strong> {nf_doc.name}</li>
        <li><strong>Chave de Acesso:</strong> {nf_doc.chave_de_acesso or '-'}</li>
        <li><strong>Supplier:</strong> {nf_doc.emitente_razao_social or nf_doc.emitente_cnpj or '-'}</li>
        <li><strong>Value:</strong> R$ {nf_doc.valor_total or 0:,.2f}</li>
    </ul>

    <h4>Documents Requiring Action:</h4>
    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">
        <tr style="background-color: #f0f0f0;">
            <th>Document Type</th>
            <th>Document</th>
            <th>Error</th>
            <th>Action Required</th>
        </tr>
        {issues_html}
    </table>

    <p>Please review and take the necessary action to cancel or adjust these linked documents.</p>

    <p><a href="{frappe.utils.get_url()}/app/nota-fiscal/{nf_doc.name}">View Nota Fiscal</a></p>
    """

    try:
        frappe.sendmail(
            recipients=[settings.alert_email],
            subject=subject,
            message=message,
            now=True
        )
        frappe.logger().info(f"Cancellation alert sent for NF {nf_doc.name}")
    except Exception as e:
        frappe.logger().error(f"Failed to send cancellation alert: {e}")


def _create_nota_fiscal_from_xml(xml_content, document_type, company_settings, chave=None, nsu=None):
    """
    Create a Nota Fiscal document from XML content.
    """
    parser = NFXMLParser()
    data = parser.parse(xml_content)

    if not data:
        raise ValueError("Failed to parse XML")

    settings = frappe.get_single("Nota Fiscal Settings")

    nf_doc = frappe.new_doc("Nota Fiscal")
    nf_doc.document_type = document_type
    nf_doc.company = company_settings.company
    nf_doc.origin_sefaz = 1

    # Set chave if provided
    if chave:
        nf_doc.chave_de_acesso = chave

    # Set NSU if provided (for duplicate detection)
    if nsu:
        nf_doc.nsu = str(nsu)

    # Extract items before processing other fields
    items_data = data.pop("items", [])

    # Populate from parsed data (excluding items which need special handling)
    for field, value in data.items():
        if hasattr(nf_doc, field) and value is not None:
            setattr(nf_doc, field, value)

    # Add items to child table
    for item_data in items_data:
        nf_doc.append("items", {
            "numero_item": item_data.get("numero_item"),
            "codigo_produto": item_data.get("codigo_produto"),
            "codigo_barras": item_data.get("codigo_barras"),
            "descricao": item_data.get("descricao"),
            "ncm": item_data.get("ncm"),
            "cfop": item_data.get("cfop"),
            "codigo_tributacao_nacional": item_data.get("codigo_tributacao_nacional"),
            "codigo_nbs": item_data.get("codigo_nbs"),
            "unidade": item_data.get("unidade"),
            "quantidade": item_data.get("quantidade"),
            "valor_unitario": item_data.get("valor_unitario"),
            "valor_total": item_data.get("valor_total"),
            "icms_cst": item_data.get("icms_cst"),
            "icms_base_calculo": item_data.get("icms_base_calculo"),
            "icms_aliquota": item_data.get("icms_aliquota"),
            "icms_valor": item_data.get("icms_valor"),
            "iss_base_calculo": item_data.get("iss_base_calculo"),
            "iss_aliquota": item_data.get("iss_aliquota"),
            "iss_valor": item_data.get("iss_valor")
        })

    nf_doc.xml_content = xml_content
    nf_doc.insert(ignore_permissions=True)

    return nf_doc.name


def test_sefaz_connection(company_settings_name):
    """
    Test connection to SEFAZ using company certificate.

    Returns:
        dict: Test result
    """
    company_settings = frappe.get_doc("NF Company Settings", company_settings_name)
    settings = frappe.get_single("Nota Fiscal Settings")

    # Get environment - company setting takes priority
    env = _get_sefaz_environment(company_settings, settings)
    env_display = "Produção" if env == "production" else "Homologação"

    # Test NFS-e endpoint (simplest)
    endpoint = SEFAZ_ENDPOINTS["nfse"][env]

    # Get decrypted password
    certificate_password = company_settings.get_certificate_password()

    with CertificateContext(company_settings.certificate_file, certificate_password) as (cert_path, key_path):
        session = requests.Session()
        session.cert = (cert_path, key_path)

        try:
            response = session.get(f"{endpoint}/0", timeout=30)

            return {
                "status": "success",
                "http_code": response.status_code,
                "message": _("Connection successful"),
                "environment": env_display,
                "endpoint": endpoint
            }
        except requests.exceptions.SSLError as e:
            return {
                "status": "error",
                "message": _("SSL Error: Certificate may be invalid"),
                "environment": env_display
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }


def send_error_alert(subject, error_message, context=None):
    """
    Send error alert email when processing errors occur.

    Args:
        subject: Email subject
        error_message: The error message/traceback
        context: Optional dict with additional context (nf_name, document_type, etc.)
    """
    settings = frappe.get_single("Nota Fiscal Settings")

    # Check if error alerts are enabled and email is configured
    if not settings.send_error_alerts:
        return

    if not settings.alert_email:
        frappe.logger().warning("Error alert not sent: No alert email configured")
        return

    context = context or {}

    # Build context HTML
    context_html = ""
    if context:
        context_items = ""
        for key, value in context.items():
            context_items += f"<li><strong>{key}:</strong> {value}</li>"
        context_html = f"<h4>Context:</h4><ul>{context_items}</ul>"

    message = f"""
    <h3>Brazil NF Processing Error</h3>

    {context_html}

    <h4>Error Details:</h4>
    <pre style="background-color: #f5f5f5; padding: 10px; border: 1px solid #ddd; overflow-x: auto;">
{error_message}
    </pre>

    <p>Please review the error log for more details.</p>

    <p><a href="{frappe.utils.get_url()}/app/error-log">View Error Log</a></p>
    """

    try:
        frappe.sendmail(
            recipients=[settings.alert_email],
            subject=f"[Brazil NF Error] {subject}",
            message=message,
            now=True
        )
        frappe.logger().info(f"Error alert sent: {subject}")
    except Exception as e:
        frappe.logger().error(f"Failed to send error alert: {e}")


def manifest_nfe(company_settings_name: str, chave_acesso: str, tipo_evento: int = 210210) -> dict:
    """Send NF-e manifestation (ciência da operação) to SEFAZ.

    Args:
        company_settings_name: NF Company Settings name
        chave_acesso: 44-digit NF-e access key
        tipo_evento: Manifestation type
            210200 = Confirmação da Operação
            210210 = Ciência da Operação (most common)
            210220 = Desconhecimento da Operação
            210240 = Operação não Realizada

    Returns:
        Dict with result status and SEFAZ response
    """
    company_settings = frappe.get_doc("NF Company Settings", company_settings_name)
    settings = frappe.get_single("Nota Fiscal Settings")

    if not company_settings.certificate_valid:
        return {"status": "error", "message": "Certificate not valid"}

    env = _get_sefaz_environment(company_settings, settings)
    endpoint = SEFAZ_ENDPOINTS["nfe"].get(env)

    if not endpoint:
        return {"status": "error", "message": f"No endpoint for NF-e in {env}"}

    certificate_password = company_settings.get_certificate_password()
    cnpj = (company_settings.cnpj or "").replace(".", "").replace("/", "").replace("-", "")
    tpAmb = "1" if env == "production" else "2"

    # Build manifestation SOAP envelope
    soap_envelope = _build_manifestation_request(tpAmb, cnpj, chave_acesso, tipo_evento)

    with CertificateContext(company_settings.certificate_file, certificate_password) as (cert_path, key_path):
        session = requests.Session()
        session.cert = (cert_path, key_path)

        headers = {
            "Content-Type": "application/soap+xml; charset=utf-8",
            "SOAPAction": "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse",
        }

        try:
            response = _request_with_retry(session, "post", endpoint, data=soap_envelope, headers=headers, timeout=60)

            if response.status_code == 429:
                return _handle_429_response(response, company_settings)

            response.raise_for_status()

            frappe.logger().info(f"NF-e Manifestation: chave={chave_acesso[:20]}..., tipo={tipo_evento}, status={response.status_code}")

            return {
                "status": "success",
                "chave_acesso": chave_acesso,
                "tipo_evento": tipo_evento,
                "http_status": response.status_code,
            }

        except Exception as e:
            frappe.log_error(str(e), f"NF-e Manifestation Error: {chave_acesso[:20]}...")
            return {"status": "error", "message": str(e)}


def _build_manifestation_request(tpAmb: str, cnpj: str, chave_acesso: str, tipo_evento: int) -> str:
    """Build SOAP envelope for NF-e manifestation."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap12:Body>
    <nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">
      <nfeDadosMsg>
        <distDFeInt versao="1.01" xmlns="http://www.portalfiscal.inf.br/nfe">
          <tpAmb>{tpAmb}</tpAmb>
          <cUFAutor>35</cUFAutor>
          <CNPJ>{cnpj}</CNPJ>
          <consChNFe>
            <chNFe>{chave_acesso}</chNFe>
          </consChNFe>
        </distDFeInt>
      </nfeDadosMsg>
    </nfeDistDFeInteresse>
  </soap12:Body>
</soap12:Envelope>"""


def auto_manifest_ciencia(nota_fiscal_name: str):
    """Auto-manifest ciência for a new NF-e."""
    try:
        nf = frappe.get_doc("Nota Fiscal", nota_fiscal_name)
        if not nf.get("chave_de_acesso") or len(nf.chave_de_acesso) != 44:
            return

        # Find the company settings for this NF
        company_settings = frappe.get_all(
            "NF Company Settings",
            filters={"company": nf.company, "certificate_valid": 1},
            pluck="name",
            limit=1,
        )
        if not company_settings:
            return

        result = manifest_nfe(company_settings[0], nf.chave_de_acesso, tipo_evento=210210)

        if result.get("status") == "success":
            frappe.db.set_value("Nota Fiscal", nota_fiscal_name, "manifestacao_status", "Ciência")
            frappe.logger().info(f"Auto-manifested ciência: {nota_fiscal_name}")
        else:
            frappe.logger().warning(f"Manifestation failed: {nota_fiscal_name} - {result.get('message')}")

        frappe.db.commit()
    except Exception as e:
        frappe.log_error(str(e), f"Auto Manifestation Error: {nota_fiscal_name}")
