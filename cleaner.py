"""
cleaner.py — Limpador de arquivos temporários do Windows.

Uso:
    python cleaner.py                    # limpeza padrão (com confirmação)
    python cleaner.py --dry-run          # só mostra o que seria apagado
    python cleaner.py --yes              # pula a confirmação
    python cleaner.py --no-prefetch      # não mexe no Prefetch
    python cleaner.py --no-recycle       # não esvazia a lixeira
    python cleaner.py --log limpeza.log  # salva log em arquivo

Requer: Python 3.8+  |  Windows 10/11
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------

log = logging.getLogger("cleaner")


def configurar_logging(arquivo: str | None) -> None:
    """Configura handlers de log: console (INFO) + arquivo opcional (DEBUG)."""
    log.setLevel(logging.DEBUG)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    log.addHandler(console)

    if arquivo:
        file_handler = logging.FileHandler(arquivo, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        log.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    """Retorna True se o processo está elevado."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def formatar_bytes(n: float) -> str:
    """Converte bytes para string legível (KB, MB, GB, TB)."""
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unidade}"
        n /= 1024
    return f"{n:.2f} PB"


def tamanho_pasta(caminho: Path) -> int:
    """Soma o tamanho de todos os arquivos de uma pasta (recursivo)."""
    total = 0
    for raiz, _, arquivos in os.walk(caminho, onerror=lambda _: None):
        for nome in arquivos:
            try:
                total += os.path.getsize(os.path.join(raiz, nome))
            except (OSError, FileNotFoundError):
                pass
    return total


# ---------------------------------------------------------------------------
# Resultado de uma limpeza
# ---------------------------------------------------------------------------

@dataclass
class Resultado:
    nome: str
    arquivos_ok: int = 0
    arquivos_falha: int = 0
    pastas_ok: int = 0
    pastas_falha: int = 0
    bytes_liberados: int = 0
    pulado: bool = False
    erro: str | None = None
    detalhes: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        if self.pulado:
            return f"  [PULADO] {self.erro or 'motivo desconhecido'}"
        linhas = [
            f"  Arquivos removidos: {self.arquivos_ok} "
            f"(falhas: {self.arquivos_falha})",
            f"  Pastas removidas:   {self.pastas_ok} "
            f"(falhas: {self.pastas_falha})",
            f"  Espaço liberado:    {formatar_bytes(self.bytes_liberados)}",
        ]
        return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Rotinas de limpeza
# ---------------------------------------------------------------------------

def limpar_pasta(
    nome: str,
    caminho: str | Path,
    *,
    remover_pastas: bool = True,
    dry_run: bool = False,
) -> Resultado:
    """Remove arquivos (e opcionalmente subpastas) de uma pasta."""
    res = Resultado(nome=nome)
    pasta = Path(caminho)

    if not pasta.exists():
        res.pulado = True
        res.erro = f"caminho não existe: {pasta}"
        log.warning("  [SKIP] %s", res.erro)
        return res

    log.info("  Limpando: %s", pasta)

    # Protege o próprio iterdir() contra PermissionError
    try:
        itens = list(pasta.iterdir())
    except PermissionError:
        res.pulado = True
        res.erro = f"acesso negado: {pasta} (rode como Administrador)"
        log.warning("  [SKIP] %s", res.erro)
        return res
    except OSError as e:
        res.pulado = True
        res.erro = f"erro ao listar {pasta}: {e}"
        log.warning("  [SKIP] %s", res.erro)
        return res

    for item in itens:
        try:
            if item.is_file() or item.is_symlink():
                try:
                    tam = item.stat().st_size
                except OSError:
                    tam = 0

                if dry_run:
                    log.debug("    [dry-run] arquivo: %s (%s)",
                              item, formatar_bytes(tam))
                    res.arquivos_ok += 1
                    res.bytes_liberados += tam
                    continue

                item.unlink()
                res.arquivos_ok += 1
                res.bytes_liberados += tam
                log.debug("    removido: %s", item)

            elif item.is_dir() and remover_pastas:
                tam = tamanho_pasta(item)

                if dry_run:
                    log.debug("    [dry-run] pasta: %s (%s)",
                              item, formatar_bytes(tam))
                    res.pastas_ok += 1
                    res.bytes_liberados += tam
                    continue

                shutil.rmtree(item, ignore_errors=False)
                res.pastas_ok += 1
                res.bytes_liberados += tam
                log.debug("    removida pasta: %s", item)

        except PermissionError:
            res.arquivos_falha += 1
            res.detalhes.append(f"sem permissão: {item.name}")
        except OSError as e:
            res.arquivos_falha += 1
            res.detalhes.append(f"erro em {item.name}: {e}")

    return res


def limpar_temp_usuario(dry_run: bool) -> Resultado:
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if not temp:
        r = Resultado(nome="Temp do usuário")
        r.pulado = True
        r.erro = "variável TEMP não definida"
        return r
    return limpar_pasta("Temp do usuário", temp, dry_run=dry_run)


def limpar_temp_windows(dry_run: bool) -> Resultado:
    return limpar_pasta(
        "Temp do Windows",
        r"C:\Windows\Temp",
        dry_run=dry_run,
    )


def limpar_prefetch(dry_run: bool) -> Resultado:
    # Prefetch não tem subpastas — não vale a pena tentar remover
    return limpar_pasta(
        "Prefetch",
        r"C:\Windows\Prefetch",
        remover_pastas=False,
        dry_run=dry_run,
    )


def limpar_lixeira(dry_run: bool) -> Resultado:
    """Esvazia a lixeira via API nativa do Windows."""
    res = Resultado(nome="Lixeira")

    if dry_run:
        log.info("  [dry-run] esvaziaria a lixeira")
        res.pulado = True
        res.erro = "modo dry-run"
        return res

    try:
        # SHEmptyRecycleBinW: flags 0x7 = sem confirmação + sem progresso + sem som
        resultado = ctypes.windll.shell32.SHEmptyRecycleBinW(
            None, None, 0x00000007
        )
        codigo = resultado & 0xFFFFFFFF

        if codigo == 0:
            log.info("  Lixeira esvaziada.")
        elif codigo == 0x8000FFFF:
            # E_UNEXPECTED — geralmente significa "já está vazia"
            log.info("  Lixeira já estava vazia.")
        elif codigo == 0x80070005:
            log.warning("  Acesso negado à lixeira (rode como Administrador).")
            res.erro = "acesso negado"
        else:
            log.warning("  Aviso ao esvaziar lixeira: código 0x%08X", codigo)
            res.erro = f"código 0x{codigo:08X}"
    except Exception as e:  # noqa: BLE001
        res.erro = str(e)
        log.error("  Falha ao esvaziar lixeira: %s", e)

    return res


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------

def executar(args: argparse.Namespace) -> int:
    inicio = datetime.now()
    admin = is_admin()

    log.info("=" * 60)
    log.info("  LIMPEZA DE ARQUIVOS TEMPORÁRIOS")
    log.info("  Início: %s", inicio.strftime("%d/%m/%Y %H:%M:%S"))
    if args.dry_run:
        log.info("  MODO DRY-RUN — nada será apagado de verdade")
    log.info("=" * 60)

    if not admin:
        log.warning(
            "  AVISO: rodando sem privilégios de administrador.\n"
            "         Etapas de sistema (Temp do Windows e Prefetch)\n"
            "         serão puladas automaticamente."
        )

    alvos: list[tuple[str, Callable[[bool], Resultado]]] = [
        ("Temp do usuário", limpar_temp_usuario),
    ]

    if admin and not args.no_windows_temp:
        alvos.append(("Temp do Windows", limpar_temp_windows))
    elif not admin:
        log.info("")
        log.info("[SKIP] Temp do Windows — requer Administrador")

    if admin and not args.no_prefetch:
        alvos.append(("Prefetch", limpar_prefetch))
    elif not admin:
        log.info("[SKIP] Prefetch — requer Administrador")

    if not args.no_recycle:
        alvos.append(("Lixeira", limpar_lixeira))

    resultados: list[Resultado] = []

    for i, (nome, func) in enumerate(alvos, start=1):
        log.info("")
        log.info("[%d/%d] %s", i, len(alvos), nome)
        try:
            r = func(args.dry_run)
            resultados.append(r)
            log.info(r.resumo())
            for det in r.detalhes[:5]:
                log.info("      • %s", det)
        except Exception as e:  # noqa: BLE001
            log.exception("  Erro inesperado em %s: %s", nome, e)
            resultados.append(Resultado(nome=nome, erro=str(e)))

    # ---- Resumo final ----
    fim = datetime.now()
    total_bytes = sum(r.bytes_liberados for r in resultados)
    total_arq = sum(r.arquivos_ok for r in resultados)
    total_pastas = sum(r.pastas_ok for r in resultados)

    log.info("")
    log.info("=" * 60)
    log.info("  RESUMO FINAL")
    log.info("=" * 60)
    log.info("  Arquivos removidos: %d", total_arq)
    log.info("  Pastas removidas:   %d", total_pastas)
    log.info("  Espaço liberado:    %s", formatar_bytes(total_bytes))
    log.info("  Duração:            %.2f s", (fim - inicio).total_seconds())
    log.info("=" * 60)

    if args.dry_run:
        log.info("  (nada foi apagado — modo dry-run)")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cleaner",
        description="Limpa arquivos temporários do Windows com segurança.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  python cleaner.py --dry-run\n"
            "  python cleaner.py --yes --log limpeza.log\n"
            "  python cleaner.py --no-prefetch\n"
        ),
    )
    p.add_argument("--dry-run", action="store_true",
                   help="não apaga nada, só mostra o que seria removido")
    p.add_argument("-y", "--yes", action="store_true",
                   help="pula a confirmação interativa")
    p.add_argument("--no-prefetch", action="store_true",
                   help="não limpa o Prefetch (recomendado deixar intacto)")
    p.add_argument("--no-windows-temp", action="store_true",
                   help="não limpa C:\\Windows\\Temp")
    p.add_argument("--no-recycle", action="store_true",
                   help="não esvazia a lixeira")
    p.add_argument("--log", metavar="ARQUIVO",
                   help="salva o log em arquivo (ex: limpeza.log)")
    return p.parse_args(argv)


def confirmar() -> bool:
    try:
        r = input("\nConfirmar limpeza? (s/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return r in ("s", "sim", "y", "yes")


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        print("Este programa é exclusivo para Windows.", file=sys.stderr)
        return 1

    args = parse_args(argv)
    configurar_logging(args.log)

    if not args.dry_run and not args.yes:
        if not confirmar():
            log.info("Cancelado pelo usuário.")
            return 0

    return executar(args)


if __name__ == "__main__":
    sys.exit(main())