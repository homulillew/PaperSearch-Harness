$env:PYTHONUTF8 = "1"
$base = "c:\Users\wushuhong\Desktop\literature-research-clean-e2e-20260810-195219\workspace"

# Load all search result files
$files = @{
    "broad" = "$base\search_broad.json"
    "eviction" = "$base\search_eviction.json"
    "quant" = "$base\search_quant.json"
    "arch" = "$base\search_arch.json"
    "spec" = "$base\search_spec.json"
    "sparse" = "$base\search_sparse.json"
    "prefix" = "$base\search_prefix.json"
}

# The broad search was the first one - reconstruct from the persisted output
# Actually the first search output was in the tool-results file. Let me load what we have.
$allHits = @{}

foreach ($kv in $files.GetEnumerator()) {
    if (Test-Path $kv.Value) {
        $raw = Get-Content $kv.Value -Raw
        # Skip PowerShell error wrapper lines
        if ($raw -match '(?s)\{"command".*') {
            $raw = [regex]::Match($raw, '(?s)\{"command".*').Value
        }
        try {
            $obj = $raw | ConvertFrom-Json
            if ($obj.result -and $obj.result.hits) {
                foreach ($h in $obj.result.hits) {
                    if ($h.arxiv_id -and -not $allHits.ContainsKey($h.arxiv_id)) {
                        $allHits[$h.arxiv_id] = $h
                    }
                }
            }
        } catch {
            Write-Output "Parse error on $($kv.Key): $($_.Exception.Message)"
        }
    }
}

Write-Output "Total unique hits: $($allHits.Count)"

# Select representative papers by arxiv_id for each route
$selected = @(
    # Surveys (route context)
    "2603.20397",  # 2026 survey - KV Cache Optimization Strategies
    "2412.19442",  # Survey on LLM Acceleration based on KV Cache Management
    "2407.18003",  # Keep the Cost Down review

    # Eviction / token selection route
    "2503.08879",  # SAGE-KV (2025)
    "2503.12491",  # CAKE (2025)
    "2502.14051",  # RocketKV (2025)
    "2504.15364",  # KeyDiff (2025)
    "2506.15969",  # LazyEviction (2025) - reasoning
    "2605.06676",  # LKV (2026) - end-to-end learned eviction
    "2602.10238",  # KV Policy / KVP (2026) - RL eviction
    "2605.09649",  # Make Each Token Count (2026) - global retention
    "2605.25475",  # IndexMem (2026) - latent memory
    "2605.07234",  # LaProx (2026) - output-aware eviction
    "2512.00504",  # G-KV (2025) - global attention reasoning

    # Quantization / compression route
    "2502.04420",  # KVTuner (2025) - mixed precision
    "2506.18879",  # CommVQ (2025) - vector quantization
    "2603.16435",  # VQKV (2026) - vector quantization
    "2603.27467",  # TurboAngle (2026) - angle quantization
    "2605.02905",  # eOptShrinkQ (2026) - spectral denoising
    "2605.17613",  # VeriCache (2026) - lossy to lossless
    "2512.05916",  # KQ-SVD (2025) - provable low-rank
    "2604.11501",  # Quantization Dominates Rank Reduction (2026)
    "2503.10337",  # KV-Distill (2025) - learnable context compression
    "2510.00636",  # Expected Attention (2025) - future query distribution

    # Sparse attention route
    "2502.11089",  # NSA - Native Sparse Attention (DeepSeek, 2025)
    "2602.03216",  # Token Sparse Attention (2026)
    "2602.05191",  # Double-P (2026)
    "2601.22379",  # SPLA (2026) - block sparse + linear
    "2605.27740",  # UNIQUE (2026) - universal top-k
    "2605.24168",  # Inference Time Context Sparsity (2026)
    "2502.06766",  # MoBA / Exploiting Sparsity (2025) - million token
    "2512.16391",  # Kascade (2025) - practical sparse attention

    # Architecture alternatives (linear/SSM/hybrid)
    "2510.07019",  # Native Hybrid Attention (2025)
    "2602.11761",  # MiniCPM-SALA (2026) - sparse+linear
    "2603.15569",  # Mamba-3 (2026)
    "2508.15099",  # Hydra (2025) - modular long-context
    "2509.00202",  # TConstFormer (2025) - O(1) KV cache
    "2605.06997",  # Echo (2026) - KV-cache-free associative recall

    # Prefix sharing / caching systems
    "2510.09665",  # LMCache (2025) - enterprise KV caching
    "2502.16002",  # KVLink (2025) - KV cache reuse
    "2604.03143",  # TokenDance (2026) - multi-agent sharing
    "2605.24022",  # Adaptive KV Cache Reuse (2026)
    "2604.13226",  # KV Packet (2026) - context-independent caching
    "2604.25080",  # CacheFlow (2026) - 3D parallel restoration
    "2604.06370",  # ForkKV (2026) - copy-on-write disaggregated
    "2603.23049",  # PCR (2026) - prefetch RAG serving

    # Speculative decoding x cache
    "2502.10424",  # QuantSpec (2025) - hierarchical quantized
    "2605.02888",  # SpecKV (2026) - compression-aware gamma
    "2602.07223",  # SpecAttn (2026) - co-design sparse + spec
    "2604.26412",  # When Hidden States Drift (2026) - long-range spec

    # System / scheduling / offloading
    "2502.07115",  # Online Scheduling with KV Cache Constraints (2025)
    "2508.13231",  # Dynamic KV Cache Placement heterogeneous memory (2025)
    "2507.19823",  # HCAttention (2025) - extreme compression 4M tokens
    "2602.09725",  # KVFetcher (2026) - GPU-native codec remote fetch
    "2507.14204",  # LaCache (2025) - ladder-shaped caching

    # Cross-layer / architectural compression
    "2604.13556",  # YOCO++ (2026) - cross-layer KV residual
    "2508.16134",  # CommonKV (2025) - cross-layer parameter sharing
    "2503.01586",  # EliteKV (2025) - RoPE freq + low-rank

    # Privacy (relevant cross-cutting)
    "2508.09442"   # KV-Cloak (2025) - privacy
)

$hitsToRetain = @()
foreach ($id in $selected) {
    if ($allHits.ContainsKey($id)) {
        $h = $allHits[$id]
        $hitsToRetain += [PSCustomObject]@{
            title = $h.title
            publication_year = $h.publication_year
            publication_date = $h.publication_date
            arxiv_id = $h.arxiv_id
            canonical_url = $h.canonical_url
            other_identifiers = @{}
            categories = $h.categories
        }
    } else {
        Write-Output "MISSING from search results: $id"
    }
}

Write-Output "Retaining $($hitsToRetain.Count) papers"

$retainObj = [PSCustomObject]@{ hits = $hitsToRetain }
$json = $retainObj | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText("$base\retain_batch1.json", $json, [System.Text.UTF8Encoding]::new($false))
Write-Output "Wrote retain_batch1.json"
