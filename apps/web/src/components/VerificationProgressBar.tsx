'use client'

interface VerificationProgressBarProps {
  progress: number // 0-1
  currentStep: string | null
  estimatedRemainingSeconds: number | null
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `约 ${Math.ceil(seconds)} 秒`
  const minutes = Math.floor(seconds / 60)
  const secs = Math.ceil(seconds % 60)
  if (minutes < 60) return secs > 0 ? `约 ${minutes} 分 ${secs} 秒` : `约 ${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  return `约 ${hours} 小时 ${mins} 分`
}

const STEP_LABELS: Record<string, string> = {
  lattice_constant: '晶格常数',
  cohesive_energy: '结合能',
  elastic_constants: '弹性常数',
  bulk_modulus: '体积模量',
  shear_modulus: '剪切模量',
  vacancy_formation_energy: '空位形成能',
  interstitial_formation_energy: '间隙形成能',
  surface_energy: '表面能',
  melting_point: '熔点',
  formation_energy: '形成能',
  thermal_expansion: '热膨胀系数',
  specific_heat: '比热容',
  density: '密度',
  initializing: '初始化',
  preparing: '准备计算环境',
  running_lammps: '运行 LAMMPS 计算',
  collecting_results: '收集计算结果',
  generating_report: '生成报告',
}

function stepLabel(step: string | null): string {
  if (!step) return '处理中...'
  return STEP_LABELS[step] || step
}

export default function VerificationProgressBar({
  progress,
  currentStep,
  estimatedRemainingSeconds,
}: VerificationProgressBarProps) {
  const pct = Math.min(100, Math.max(0, progress * 100))

  return (
    <div className="space-y-3">
      {/* Progress bar */}
      <div className="w-full bg-gray-700 rounded-full h-3 overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all duration-500 ease-out relative"
          style={{ width: `${pct}%` }}
        >
          {/* Animated shimmer */}
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent animate-pulse" />
        </div>
      </div>

      {/* Info row */}
      <div className="flex items-center justify-between text-sm">
        <div className="flex items-center gap-2">
          <span className="animate-spin inline-block w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full" />
          <span className="text-gray-300">{stepLabel(currentStep)}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-blue-400 font-mono font-medium">{pct.toFixed(0)}%</span>
          {estimatedRemainingSeconds != null && estimatedRemainingSeconds > 0 && (
            <span className="text-gray-400">
              剩余 {formatDuration(estimatedRemainingSeconds)}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
