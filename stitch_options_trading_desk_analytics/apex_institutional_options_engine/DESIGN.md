---
name: Apex Institutional Options Engine
colors:
  surface: '#051424'
  surface-dim: '#051424'
  surface-bright: '#2c3a4c'
  surface-container-lowest: '#010f1f'
  surface-container-low: '#0d1c2d'
  surface-container: '#122131'
  surface-container-high: '#1c2b3c'
  surface-container-highest: '#273647'
  on-surface: '#d4e4fa'
  on-surface-variant: '#b9cacb'
  inverse-surface: '#d4e4fa'
  inverse-on-surface: '#233143'
  outline: '#849495'
  outline-variant: '#3a494b'
  surface-tint: '#00dce6'
  primary: '#e0fdff'
  on-primary: '#00373a'
  primary-container: '#00f2fe'
  on-primary-container: '#006a70'
  inverse-primary: '#00696f'
  secondary: '#7dffa2'
  on-secondary: '#003918'
  secondary-container: '#05e777'
  on-secondary-container: '#00622e'
  tertiary: '#fff5f5'
  on-tertiary: '#670020'
  tertiary-container: '#ffcfd3'
  on-tertiary-container: '#c00043'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#6ff6ff'
  primary-fixed-dim: '#00dce6'
  on-primary-fixed: '#002022'
  on-primary-fixed-variant: '#004f53'
  secondary-fixed: '#62ff96'
  secondary-fixed-dim: '#00e475'
  on-secondary-fixed: '#00210b'
  on-secondary-fixed-variant: '#005226'
  tertiary-fixed: '#ffd9dc'
  tertiary-fixed-dim: '#ffb2ba'
  on-tertiary-fixed: '#400011'
  on-tertiary-fixed-variant: '#910030'
  background: '#051424'
  on-background: '#d4e4fa'
  surface-variant: '#273647'
typography:
  headline-xl:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Geist
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Geist
    fontSize: 15px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Geist
    fontSize: 13px
    fontWeight: '600'
    lineHeight: 18px
    letterSpacing: -0.005em
  body-md:
    fontFamily: Geist
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  body-sm:
    fontFamily: Geist
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  data-mono-lg:
    fontFamily: JetBrains Mono
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: -0.02em
  data-mono-md:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: -0.01em
  data-mono-sm:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0em
  data-mono-xs:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '500'
    lineHeight: 12px
    letterSpacing: 0.02em
  label-md:
    fontFamily: Geist
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.04em
  label-xs:
    fontFamily: JetBrains Mono
    fontSize: 9px
    fontWeight: '600'
    lineHeight: 12px
    letterSpacing: 0.06em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 0.25rem
  margin: 0.5rem
  space-xs: 0.125rem
  space-sm: 0.25rem
  space-md: 0.5rem
  space-lg: 0.75rem
  space-xl: 1rem
---

## Brand & Style
The design system delivers an institutional-grade, high-density options trading environment engineered for quantitative traders, market makers, and derivatives analysts. The visual language merges the ruthless efficiency and diagnostic precision of legacy terminals (Bloomberg, ThinkorSwim) with the refined, luminous aesthetic of modern Web3 interfaces and TradingView chart engines.

The interface prioritizes maximum information density, instant visual scanning, absolute numerical legibility, and sub-millisecond perceived latency. Interactions are snappy and tactical; visual noise is eliminated in favor of strict data hierarchies, glowing status indicators, and crisp micro-geometry.

Key visual tenets:
- **Obsidian Tonal Matrix**: Pure dark canvas anchored by deep slate and obsidian surfaces, preventing eye fatigue during extended trading sessions while providing infinite contrast for neon telemetry.
- **Precision Luminescence**: Strategic, high-chroma semantic accents (cyan, emerald, crimson, amber) reserved strictly for actionable states, order execution paths, risk telemetry, and delta/strike gradients.
- **Instrumental Modernism**: Hairline structural grids, monospaced tabular data structures, micro-badges, and compact toggle arrays that evoke advanced avionics and mission-critical cockpits.

## Colors
The color architecture relies on a specialized palette tailored for real-time market data visualization and derivatives execution.

### Background & Surface Hierarchy
- **Canvas Base (`#080B11`)**: Deepest obsidian, used for application frame backgrounds and root layout viewports.
- **Surface Level 1 (`#0E131F`)**: Dark slate, used for discrete dockable panels, terminal panes, and data table bodies.
- **Surface Level 2 (`#161D2F`)**: Elevated surface for toolbars, strike ladder center rails, pinned rows, popovers, and table headers.
- **Surface Active / Hover (`#1E293B`)**: Interaction state for hovered rows, active inputs, and elevated control chips.
- **Subtle Hairline Divider (`#1E293B`)**: Structural 1px boundary separating workspaces, panels, and data columns.

### Semantics & Telemetry
- **Primary Accent (`#00F2FE` - Cyber Cyan)**: Primary active triggers, selection rings, current price lines, focus states, and primary command vectors.
- **Bullish / Calls / Bid / PnL Positive (`#00E676` - Vivid Emerald)**: Long exposure, call options, bid volume bars, positive delta, and profitable yield runs. Background tinted badge fill: `rgba(0, 230, 118, 0.12)`.
- **Bearish / Puts / Ask / PnL Negative (`#FF3366` - Vivid Crimson/Coral)**: Short exposure, put options, ask volume bars, negative delta, and critical liquidation risks. Background tinted badge fill: `rgba(255, 51, 102, 0.12)`.
- **Warning / At-The-Money (`#FFB300` - Electric Amber)**: At-the-money (ATM) strike highlights, liquidity imbalances, pending orders, and delta slippage warnings. Background tinted badge fill: `rgba(255, 179, 0, 0.12)`.

### Typography Neutrals
- **Text Primary (`#F8FAFC`)**: High-contrast labels, active strike numbers, and executed trade quantities.
- **Text Secondary (`#94A3B8`)**: Field identifiers, standard column headers, inactive tab items, and secondary Greek metrics.
- **Text Muted (`#475569`)**: Disabled controls, inactive contract months, grid ticks, and watermark indicators.

## Typography
The system employs a dual-font strategy optimized for extreme legibility under high cognitive load:
1. **Primary Interface Font (`Geist`)**: Used for workspace headers, contextual tooltips, action triggers, navigation tabs, and system messaging. Its high x-height and mechanical geometric proportions ensure rapid legibility at tiny sizes.
2. **Numeric & Telemetry Engine (`JetBrains Mono`)**: Strict tabular lining figures applied to all price displays, strike ladders, order books, Greeks (Delta, Gamma, Vega, Theta, Rho), volume histograms, and execution timestamps. Monospaced alignment ensures that fluctuating values do not cause visual jitter across continuous streaming feeds.

All labels and column descriptors use uppercase styling with subtle letter-spacing (`0.04em` to `0.06em`) to establish crisp contrast against dynamic numerical streams.

## Layout & Spacing
The layout operates on an ultra-compact 4px baseline rhythm optimized for multi-pane trading environments spanning ultra-wide desktop monitors (1440px to 4K displays).

### Grid & Pane Architecture
- **Root Shell**: Full viewport height (`100vh`) with fixed perimeter margins (`margin: 0.5rem`), divided into non-overlapping, resizable dock panels.
- **Top Telemetry Header**: Fixed 44px bar hosting asset selector, ticker summary (Underlying, IV Rank, 24h High/Low), account equity metrics, and socket latency indicators.
- **Split Workspaces**:
  - **Left Wing (280px - 340px)**: Watchlists, positions, and order blotter.
  - **Center Deck (Flex 1 1 0%)**: Top partition for payoff curve diagrams & interactive surface charts (IV skew); bottom partition for the bidirectional Call/Strike/Put chain ladder.
  - **Right Wing (320px - 380px)**: Order ticket execution entry, real-time Level 2 depth, and Greeks matrix telemetry panel.
- **Gutter Rule**: Panes and modular blocks are separated by razor-thin `0.25rem` (4px) gutters or single 1px hairline border intersections to preserve 98% active data area.

## Elevation & Depth
Elevation in this high-density terminal is achieved through calibrated surface luminosity, crisp 1px borders, and localized ambient glow rather than physical drop shadows.

1. **Layer 0 (Canvas Base - `#080B11`)**: Deepest recessed plane; houses workspace split tracks and inactive chart viewports.
2. **Layer 1 (Standard Panel - `#0E131F`)**: Default container for data grids, chain trees, and watchlists. Enclosed by a 1px solid border of `#1E293B`.
3. **Layer 2 (Interactive Floating Modules - `#161D2F`)**: Hovered strike cards, active dropdown overlays, context menus, and drag-and-drop dock anchors. Uses a crisp border (`#2D3B55`) and an ambient, low-spread glow: `box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.7)`.
4. **Active Strike / Critical State Glow**:
   - Primary focus: `0 0 10px rgba(0, 242, 254, 0.25)` with border `#00F2FE`.
   - Bullish execution: `0 0 8px rgba(0, 230, 118, 0.2)` with border `#00E676`.
   - Bearish execution: `0 0 8px rgba(255, 51, 102, 0.2)` with border `#FF3366`.
   - ATM Straddle Marker: `0 0 8px rgba(255, 179, 0, 0.25)` with border `#FFB300`.

## Shapes
The system utilizes a compact, utilitarian curvature model (`roundedness: 1`):
- **Base Components (Inputs, Table Rows, Strike Cards, Action Triggers)**: `0.25rem` (4px) corner radius to retain an architectural, instrument-like silhouette.
- **Dockable Window Containers & Chart Wrappers**: `0.375rem` (6px) outer edge boundary to subtly soften screen intersections.
- **Telemetry Chips, Badges, and Pill Toggles**: Fully rounded pill forms (`9999px`) to create rapid distinction between interactive state filters (e.g., expiry dates, Call/Put selectors) and structured tabular cells.
- **Structural Partition Borders**: Zero-radius hairline joints (`0px`) where data grid boundaries meet side rails.

## Components

### Buttons & Action Triggers
- **Primary Execution Button**: Height 28px or 32px. Filled `#00F2FE` text `#080B11`, font `Geist` 600. On hover: luminosity boost with `box-shadow: 0 0 12px rgba(0, 242, 254, 0.4)`. Active: scale `0.99`.
- **Buy / Long Call Button**: Solid emerald `#00E676` background, black text `#041A0E`, font `Geist` 600.
- **Sell / Short Put Button**: Solid crimson `#FF3366` background, white text `#FFFFFF`, font `Geist` 600.
- **Ghost / Tool Bar Actions**: Transparent background, border 1px solid transparent, text `#94A3B8`. Hover: border `#1E293B`, background `#161D2F`, text `#F8FAFC`.

### Options Strike Ladder (Bidirectional Matrix)
- **Structure**: Center-pinned monospaced Strike Price column on elevated `#161D2F`, flanked by Calls on the left and Puts on the right.
- **ATM (At-The-Money) Row**: Highlighted with an electric amber left-to-right indicator border (`#FFB300`), muted amber linear fill overlay (`rgba(255, 179, 0, 0.08)`), and bold `data-mono-md` strike text.
- **In-The-Money (ITM) Shading**: Calls ITM shaded with `rgba(0, 230, 118, 0.04)`; Puts ITM shaded with `rgba(255, 51, 102, 0.04)`.
- **Row Heights**: Fixed 22px micro-rows for high contract density. Cell padding `0px 6px`.

### Badges & Micro-Telemetry Chips
- **Status Pills**: Height 16px. Border radius `9999px`. Font `label-xs` (`9px JetBrains Mono`).
  - *Call Badge*: Border `rgba(0, 230, 118, 0.3)`, fill `rgba(0, 230, 118, 0.1)`, text `#00E676`.
  - *Put Badge*: Border `rgba(255, 51, 102, 0.3)`, fill `rgba(255, 51, 102, 0.1)`, text `#FF3366`.
  - *IV Rank Indicator*: Border `rgba(0, 242, 254, 0.3)`, fill `rgba(0, 242, 254, 0.1)`, text `#00F2FE`.

### Pill Toggles & Expiry Strips
- **Segmented Expiry Selector**: Horizontal scrolling bar with 20px height pill buttons.
- **Inactive Expiry**: Text `#94A3B8`, background `#0E131F`, border 1px solid `#1E293B`.
- **Active Expiry**: Text `#00F2FE`, background `#161D2F`, border 1px solid `#00F2FE`. Displays DTE (Days to Expiry) badge in monospaced secondary text.

### Input Fields & Order Sliders
- **Numeric Order Stepper**: Height 26px. JetBrains Mono 12px right-aligned value. Background `#080B11`, border 1px solid `#1E293B`. Inner increment/decrement chevron buttons embedded flush. Focus state: border `#00F2FE`.
- **Limit/Market Pill Group**: Ultra-compact 20px dual switch with 2px inset active slate indicator.

### Greeks Telemetry Panel
- **Card Matrix**: Multi-column compact grid showing Delta, Gamma, Theta, Vega, and Rho.
- **Layout**: Top row carries uppercase 9px micro-label; bottom row renders signed real-time numeric stream (`+0.421`, `-18.40θ`) in `data-mono-md`.
- **Visual Bar**: 2px horizontal micro-gauge at the base of each card indicating net directional exposure.

### Payoff Curve & Analytics Viewer
- **Zero-Line**: 1px dashed `#475569`.
- **Breakeven Crossings**: Vertical marker lines colored `#FFB300` with floating tooltips rendering exact expiry breakeven values.
- **Profit Area Fill**: Linear gradient from `rgba(0, 230, 118, 0.25)` to transparent.
- **Loss Area Fill**: Linear gradient from `rgba(255, 51, 102, 0.25)` to transparent.