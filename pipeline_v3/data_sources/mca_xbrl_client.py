import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCAXBRLArtifact:
    path: str
    kind: str  # "xbrl" | "xml" | "zip"
    fiscal_year: Optional[str] = None


class MCAXBRLClient:
    """
    Tier-1: MCA AOC-4 XBRL access.

    Production reality:
    - MCA filings often require authentication / paid access / captcha and are not reliably scrapable.
    - This client supports a robust LOCAL mode: point it at a directory of downloaded AOC-4 XBRL artifacts.
    - A future remote provider can be added without changing the rest of the pipeline.
    """

    def __init__(self, base_dir: str = "storage/raw/mca_xbrl"):
        self.base_dir = Path(base_dir)

    def list_artifacts(self, *, cin: str) -> List[MCAXBRLArtifact]:
        root = self.base_dir / cin.upper()
        if not root.exists():
            return []

        artifacts: List[MCAXBRLArtifact] = []
        for p in sorted(root.rglob("*")):
            if p.is_dir():
                continue
            low = p.name.lower()
            if low.endswith(".zip"):
                artifacts.append(MCAXBRLArtifact(path=str(p), kind="zip"))
            elif low.endswith(".xbrl") or low.endswith(".xml"):
                artifacts.append(MCAXBRLArtifact(path=str(p), kind="xbrl" if low.endswith(".xbrl") else "xml"))
        return artifacts

    def load_xbrl_xml_bytes(self, artifact_path: str) -> Tuple[Optional[bytes], Dict]:
        """
        Returns XBRL XML bytes and lightweight metadata.
        Supports .xml/.xbrl files directly and .zip containers.
        """
        p = Path(artifact_path)
        if not p.exists():
            return None, {"error": "not_found", "path": artifact_path}

        if p.suffix.lower() in (".xml", ".xbrl"):
            try:
                return p.read_bytes(), {"path": str(p), "container": "file"}
            except Exception as e:
                return None, {"error": str(e), "path": str(p)}

        if p.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(p, "r") as zf:
                    # Prefer .xbrl/.xml; choose the largest candidate (usually the instance document).
                    candidates = [n for n in zf.namelist() if n.lower().endswith((".xbrl", ".xml"))]
                    if not candidates:
                        return None, {"error": "no_xml_in_zip", "path": str(p)}
                    best = max(candidates, key=lambda n: zf.getinfo(n).file_size)
                    return zf.read(best), {"path": str(p), "container": "zip", "member": best}
            except Exception as e:
                return None, {"error": str(e), "path": str(p)}

        return None, {"error": "unsupported_type", "path": str(p)}

