#!/bin/bash
# check-ollama-ready.sh - Pre-flight checker for Ollama installation

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "╔═══════════════════════════════════════════════════════════════════════════╗"
echo "║                    OLLAMA PRE-FLIGHT CHECK                                ║"
echo "╚═══════════════════════════════════════════════════════════════════════════╝"
echo ""

PASS=0
WARN=0
FAIL=0

check_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((PASS++))
}

check_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    ((WARN++))
}

check_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((FAIL++))
}

# Check 1: Disk space
echo "Checking disk space..."
AVAIL_GB=$(df -BG /home/geni | tail -1 | awk '{print $4}' | sed 's/G//')
if [ "$AVAIL_GB" -ge 15 ]; then
    check_pass "Disk space: ${AVAIL_GB}GB available (need ~11GB for models)"
elif [ "$AVAIL_GB" -ge 10 ]; then
    check_warn "Disk space: ${AVAIL_GB}GB available (tight, but workable)"
else
    check_fail "Disk space: ${AVAIL_GB}GB available (need at least 10GB)"
fi

# Check 2: Installation directory writable
echo "Checking installation directory..."
if [ -w "$HOME/.local/bin" ] || [ ! -e "$HOME/.local/bin" ]; then
    check_pass "Can write to ~/.local/bin"
else
    check_fail "Cannot write to ~/.local/bin"
fi

# Check 3: PATH includes ~/.local/bin
echo "Checking PATH..."
if [[ ":$PATH:" == *":$HOME/.local/bin:"* ]]; then
    check_pass "~/.local/bin is in PATH"
else
    check_warn "~/.local/bin not in PATH (install script will add it)"
fi

# Check 4: Network connectivity
echo "Checking network connectivity..."
if timeout 3 curl -s https://ollama.com > /dev/null 2>&1; then
    check_pass "Can reach ollama.com"
else
    check_warn "Cannot reach ollama.com or network is slow"
fi

# Check 5: Python availability
echo "Checking Python..."
if command -v python3 > /dev/null 2>&1; then
    PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
    check_pass "Python3 available (version $PY_VER)"
else
    check_warn "Python3 not found (tests will be skipped)"
fi

# Check 6: Systemd user mode
echo "Checking systemd user mode..."
if command -v systemctl > /dev/null 2>&1; then
    if systemctl --user list-unit-files > /dev/null 2>&1; then
        check_pass "Systemd user mode available"
    else
        check_fail "Systemd user mode not available"
    fi
else
    check_fail "systemctl not found"
fi

# Check 7: Port 11434 availability
echo "Checking port 11434..."
if ss -tln | grep -q ":11434 "; then
    check_warn "Port 11434 already in use (might be Ollama already running)"
else
    check_pass "Port 11434 is available"
fi

# Check 8: Already installed?
echo "Checking if Ollama is installed..."
if command -v ollama > /dev/null 2>&1; then
    OLLAMA_VER=$(ollama --version 2>&1 || echo "unknown")
    check_warn "Ollama already installed: $OLLAMA_VER"
else
    check_pass "Ollama not installed (fresh install)"
fi

# Check 9: Memory
echo "Checking memory..."
TOTAL_MEM_GB=$(free -g | awk '/^Mem:/ {print $2}')
if [ "$TOTAL_MEM_GB" -ge 16 ]; then
    check_pass "RAM: ${TOTAL_MEM_GB}GB (excellent for both models)"
elif [ "$TOTAL_MEM_GB" -ge 8 ]; then
    check_pass "RAM: ${TOTAL_MEM_GB}GB (good for fast model, quality model will be slow)"
else
    check_warn "RAM: ${TOTAL_MEM_GB}GB (may struggle with quality model)"
fi

# Check 10: curl availability
echo "Checking curl..."
if command -v curl > /dev/null 2>&1; then
    check_pass "curl is available"
else
    check_fail "curl not found (needed for installation)"
fi

# Summary
echo ""
echo "╔═══════════════════════════════════════════════════════════════════════════╗"
echo "║                              SUMMARY                                      ║"
echo "╚═══════════════════════════════════════════════════════════════════════════╝"
echo ""
echo -e "  ${GREEN}PASS${NC}: $PASS"
echo -e "  ${YELLOW}WARN${NC}: $WARN"
echo -e "  ${RED}FAIL${NC}: $FAIL"
echo ""

if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}✓ System is ready for Ollama installation!${NC}"
    echo ""
    echo "Next step: ./install-ollama.sh"
    exit 0
elif [ "$FAIL" -le 2 ]; then
    echo -e "${YELLOW}⚠ System has some issues but installation may still work${NC}"
    echo ""
    echo "You can try: ./install-ollama.sh"
    exit 0
else
    echo -e "${RED}✗ System has critical issues, please fix before installing${NC}"
    exit 1
fi
