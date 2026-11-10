import { useI18n } from '@/lib/i18n'
import { BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import type { Clip } from '@/lib/api'

interface QualityChartProps {
  clips: Clip[]
}

// One accent ramp for passing clips, red only for the ones below the usual threshold.
const BUCKETS = [
  { range: '90–100', min: 90, fill: 'var(--accent)' },
  { range: '80–90', min: 80, fill: 'color-mix(in srgb, var(--accent) 72%, transparent)' },
  { range: '70–80', min: 70, fill: 'color-mix(in srgb, var(--accent) 50%, transparent)' },
  { range: '60–70', min: 60, fill: 'color-mix(in srgb, var(--accent) 32%, transparent)' },
  { range: '< 60', min: -Infinity, fill: 'var(--red)' },
]

export function QualityChart({ clips }: QualityChartProps) {
  const { t } = useI18n()
  const data = BUCKETS.map((b) => ({ range: b.range, count: 0, fill: b.fill }))
  for (const clip of clips) {
    const q = clip.quality_score || 0
    const idx = BUCKETS.findIndex((b) => q >= b.min)
    data[idx].count++
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="eyebrow">{t('job.distribution')}</span>
        <span className="font-mono text-[12px] text-lo">{t('job.scored', { count: clips.length })}</span>
      </div>
      <div className="tile !p-3">
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid vertical={false} stroke="var(--border-subtle)" />
            <XAxis
              dataKey="range"
              tickLine={false}
              axisLine={{ stroke: 'var(--border-subtle)' }}
              tick={{ fill: 'var(--text-lo)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
            />
            <YAxis
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              tick={{ fill: 'var(--text-lo)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
            />
            <Tooltip
              cursor={{ fill: 'var(--bg-card-hover)' }}
              contentStyle={{
                background: 'var(--bg-raised)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 10,
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
              }}
              labelStyle={{ color: 'var(--text-hi)' }}
              itemStyle={{ color: 'var(--text-mid)' }}
              formatter={(value) => [value, t('job.clips')]}
            />
            <Bar dataKey="count" radius={[6, 6, 0, 0]} maxBarSize={56}>
              {data.map((entry, i) => (
                <Cell key={`cell-${i}`} fill={entry.fill} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
