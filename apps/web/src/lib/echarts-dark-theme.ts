/**
 * ECharts dark theme configuration for NFMD.
 *
 * Colors are derived from the CSS custom properties defined in globals.css.
 * When NFM-378 (UX Design Spec) is finalized, update these tokens to match.
 */

import type { ThemeOption } from "echarts"

/** Palette derived from globals.css dark-theme tokens */
export const DARK_PALETTE = {
  background: "#1f2937",       // --color-surface (gray-800)
  surface: "#374151",          // --color-surface-elevated (gray-700)
  textPrimary: "#f9fafb",     // --color-text (gray-50)
  textSecondary: "#9ca3af",   // --color-text-secondary (gray-400)
  accent: "#60a5fa",           // --color-accent (blue-400)
  border: "#4b5563",           // --color-border (gray-600)
  success: "#34d399",          // emerald-400
  warning: "#fbbf24",          // amber-400
  error: "#f87171",            // red-400
  info: "#60a5fa",             // blue-400 (same as accent)
  /** Chart-specific palette for categorical data */
  category: [
    "#60a5fa", // blue-400
    "#34d399", // emerald-400
    "#fbbf24", // amber-400
    "#f87171", // red-400
    "#a78bfa", // violet-400
    "#fb923c", // orange-400
    "#38bdf8", // sky-400
    "#e879f9", // fuchsia-400
  ],
} as const

/**
 * ECharts dark theme object.
 *
 * Register via `echarts.registerTheme('nfm-dark', nfmDarkTheme)`
 * and pass `theme="nfm-dark"` to <ReactECharts />.
 */
export const nfmDarkTheme: ThemeOption = {
  color: [...DARK_PALETTE.category],

  backgroundColor: "transparent",

  textStyle: {
    color: DARK_PALETTE.textSecondary,
  },

  title: {
    textStyle: {
      color: DARK_PALETTE.textPrimary,
      fontWeight: 600,
    },
    subtextStyle: {
      color: DARK_PALETTE.textSecondary,
    },
  },

  line: {
    itemStyle: {
      borderWidth: 2,
    },
    lineStyle: {
      width: 2,
    },
    symbolSize: 6,
    symbol: "circle",
    smooth: false,
  },

  radar: {
    itemStyle: {
      borderWidth: 2,
    },
    lineStyle: {
      width: 2,
    },
    symbolSize: 6,
    symbol: "circle",
    smooth: false,
  },

  bar: {
    itemStyle: {
      borderRadius: [4, 4, 0, 0],
      borderWidth: 0,
    },
  },

  pie: {
    itemStyle: {
      borderWidth: 0,
      borderColor: DARK_PALETTE.background,
    },
  },

  scatter: {
    itemStyle: {
      borderWidth: 0,
    },
    symbolSize: 8,
  },

  graph: {
    itemStyle: {
      borderWidth: 0,
    },
    lineStyle: {
      width: 2,
      color: "#aaa",
    },
  },

  map: {
    itemStyle: {
      areaColor: DARK_PALETTE.surface,
      borderColor: DARK_PALETTE.border,
    },
    label: {
      color: DARK_PALETTE.textSecondary,
    },
    emphasis: {
      itemStyle: {
        areaColor: DARK_PALETTE.accent,
      },
    },
  },

  geo: {
    itemStyle: {
      areaColor: DARK_PALETTE.surface,
      borderColor: DARK_PALETTE.border,
    },
    label: {
      color: DARK_PALETTE.textSecondary,
    },
    emphasis: {
      itemStyle: {
        areaColor: DARK_PALETTE.accent,
      },
    },
  },

  categoryAxis: {
    axisLine: {
      show: true,
      lineStyle: {
        color: DARK_PALETTE.border,
      },
    },
    axisTick: {
      show: false,
    },
    axisLabel: {
      color: DARK_PALETTE.textSecondary,
    },
    splitLine: {
      show: false,
    },
  },

  valueAxis: {
    axisLine: {
      show: true,
      lineStyle: {
        color: DARK_PALETTE.border,
      },
    },
    axisTick: {
      show: false,
    },
    axisLabel: {
      color: DARK_PALETTE.textSecondary,
    },
    splitLine: {
      lineStyle: {
        color: DARK_PALETTE.border,
        type: "dashed" as const,
      },
    },
  },

  logAxis: {
    axisLine: {
      show: true,
      lineStyle: {
        color: DARK_PALETTE.border,
      },
    },
    axisTick: {
      show: false,
    },
    axisLabel: {
      color: DARK_PALETTE.textSecondary,
    },
    splitLine: {
      lineStyle: {
        color: DARK_PALETTE.border,
        type: "dashed" as const,
      },
    },
  },

  timeAxis: {
    axisLine: {
      show: true,
      lineStyle: {
        color: DARK_PALETTE.border,
      },
    },
    axisTick: {
      show: false,
    },
    axisLabel: {
      color: DARK_PALETTE.textSecondary,
    },
    splitLine: {
      lineStyle: {
        color: DARK_PALETTE.border,
        type: "dashed" as const,
      },
    },
  },

  toolbox: {
    iconStyle: {
      borderColor: DARK_PALETTE.textSecondary,
    },
    emphasis: {
      iconStyle: {
        borderColor: DARK_PALETTE.accent,
      },
    },
  },

  legend: {
    textStyle: {
      color: DARK_PALETTE.textSecondary,
    },
  },

  tooltip: {
    backgroundColor: DARK_PALETTE.surface,
    borderColor: DARK_PALETTE.border,
    textStyle: {
      color: DARK_PALETTE.textPrimary,
    },
    extraCssText: "border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);",
  },

  axisPointer: {
    lineStyle: {
      color: DARK_PALETTE.border,
    },
    crossStyle: {
      color: DARK_PALETTE.border,
    },
    label: {
      backgroundColor: DARK_PALETTE.surface,
      color: DARK_PALETTE.textPrimary,
    },
  },
}
