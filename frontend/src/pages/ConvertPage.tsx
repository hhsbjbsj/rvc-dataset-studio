import { useI18n } from '@/lib/i18n'
import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { Upload, Download, Loader2, FileAudio, X, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { api } from '@/lib/api'
import { API_URL } from '@/lib/config'
import { RvcConversionImport } from '@/components/RvcConversionImport'
import { SelectField, Switch } from '@/components/ui'

type ConversionResult = { job_id: string; download_url: string; files_converted: number; errors: string[] }

export function ConvertPage() {
  const { t } = useI18n()
  const [files, setFiles] = useState<File[]>([])
  const [converting, setConverting] = useState(false)
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<ConversionResult>()
  const [sampleRate, setSampleRate] = useState(22050)
  const [bitDepth, setBitDepth] = useState(16)
  const [channels, setChannels] = useState('mono')
  const [normalize, setNormalize] = useState(true)

  const onDrop = useCallback((accepted: File[]) => {
    setFiles((prev) => [...prev, ...accepted])
    setResult(undefined)
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    disabled: converting,
    accept: {
      'audio/*': ['.wav', '.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.aiff', '.webm'],
      'application/octet-stream': ['.parquet', '.npy', '.arrow', '.hdf5', '.h5', '.7z'],
      'application/zip': ['.zip'],
      'application/gzip': ['.tar.gz', '.tgz'],
    },
  })

  const removeFile = (idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx))
    setResult(undefined)
  }

  const clearAll = () => {
    setFiles([])
    setResult(undefined)
  }

  const totalMb = files.reduce((sum, f) => sum + f.size, 0) / (1024 * 1024)

  const handleConvert = async () => {
    if (files.length === 0) return
    setConverting(true)
    setProgress(0)
    setResult(undefined)

    const formData = new FormData()
    files.forEach((f) => formData.append('files', f))
    formData.append('target_format', 'wav')
    formData.append('sample_rate', String(sampleRate))
    formData.append('bit_depth', String(bitDepth))
    formData.append('channels', channels)
    formData.append('normalize', String(normalize))

    try {
      const res = await api.post<ConversionResult>('/api/convert', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (e) => {
          if (e.total) setProgress(Math.round((e.loaded / e.total) * 100))
        },
      })
      setProgress(100)
      if (res.data.files_converted > 0) {
        setResult(res.data)
        toast.success(t('convert.finished', { count: res.data.files_converted }))
      } else {
        toast.error(res.data.errors?.[0] || t('convert.empty'))
      }
      if (res.data.errors?.length && res.data.files_converted > 0) {
        toast.error(t('convert.skipped', { count: res.data.errors.length }))
      }
    } catch {
      toast.error(t('convert.failed'))
    } finally {
      setConverting(false)
    }
  }

  return (
    <div className="shell max-w-3xl space-y-8 pb-12 animate-slideIn">
      <section className="pt-10 text-center sm:pt-14">
        <span className="eyebrow">{t('convert.eyebrow')}</span>
        <h1 className="display mt-5 text-[36px] sm:text-[48px]">{t('convert.title')}</h1>
        <p className="mx-auto mt-4 max-w-lg text-[15px] text-mid">
          {t('convert.intro')}
        </p>
      </section>

      <div
        {...getRootProps()}
        className={`group cursor-pointer rounded-card border border-dashed px-6 py-14 text-center transition-colors ${
          isDragActive ? 'border-accent bg-card-active' : 'border-line-strong bg-card hover:bg-card-hover'
        }`}
      >
        <input {...getInputProps()} />
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full border border-line">
          <Upload className={`h-6 w-6 transition-colors ${isDragActive ? 'text-accent' : 'text-mid group-hover:text-hi'}`} strokeWidth={1.5} />
        </div>
        <p className="mt-5 text-[16px] font-medium">{t('convert.drop')}</p>
        <p className="mt-2 font-mono text-[12px] text-lo">WAV · MP3 · FLAC · OGG · OPUS · M4A · Parquet · NPY · ZIP · TAR.GZ · 7Z</p>
      </div>

      {files.length > 0 && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <span className="eyebrow">
              {t('convert.files', { count: files.length, size: totalMb.toFixed(1) })}
            </span>
            <button onClick={clearAll} disabled={converting} className="btn-danger btn-sm">
              <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} /> {t('convert.clear')}
            </button>
          </div>
          <ul className="max-h-56 space-y-1.5 overflow-y-auto pr-1">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="tile flex items-center justify-between gap-3 !py-2.5">
                <span className="flex min-w-0 items-center gap-2.5">
                  <FileAudio className="h-4 w-4 shrink-0 text-accent" strokeWidth={1.75} />
                  <span className="truncate font-mono text-[12px] text-hi">{f.name}</span>
                  <span className="shrink-0 font-mono text-[11px] text-lo">{(f.size / (1024 * 1024)).toFixed(2)} MB</span>
                </span>
                <button onClick={() => removeFile(i)} disabled={converting} className="icon-btn !h-7 !w-7 border-transparent" aria-label={t('convert.remove', { name: f.name })}>
                  <X className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card grid grid-cols-1 gap-6 sm:grid-cols-2">
        <SelectField
          label={t('sampleRate')}
          value={sampleRate}
          onChange={setSampleRate}
          options={[
            { value: 16000, label: t('convert.rate16') },
            { value: 22050, label: t('convert.rate22') },
            { value: 44100, label: t('convert.rate44') },
          ]}
        />
        <SelectField
          label={t('convert.bitDepth')}
          value={bitDepth}
          onChange={setBitDepth}
          options={[
            { value: 16, label: '16-bit PCM' },
            { value: 24, label: '24-bit PCM' },
            { value: 32, label: t('convert.float32') },
          ]}
        />
        <SelectField
          label={t('convert.channels')}
          value={channels}
          onChange={setChannels}
          options={[
            { value: 'mono', label: t('convert.mono') },
            { value: 'stereo', label: t('convert.stereo') },
          ]}
        />
        <div className="flex items-end">
          <div className="tile w-full !py-2.5">
            <Switch label={t('convert.normalize')} description={t('convert.normalizeTo')} checked={normalize} onChange={setNormalize} />
          </div>
        </div>
      </div>

      {converting && (
        <div className="space-y-2 animate-fadeIn">
          <div className="flex justify-between">
            <span className="eyebrow">{progress < 100 ? t('convert.uploading') : t('convert.converting')}</span>
            <span className="font-mono text-[12px] text-mid">{progress}%</span>
          </div>
          <div className="meter">
            <span style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3 sm:flex-row">
        <button onClick={handleConvert} disabled={files.length === 0 || converting} className="btn-primary btn-lg flex-1">
          {converting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" strokeWidth={2} />}
          {files.length > 0 ? t('convert.startCount', { count: files.length }) : t('convert.start')}
        </button>
        {result && (
          <a href={API_URL + result.download_url} className="btn-secondary btn-lg">
            <Download className="h-4 w-4" strokeWidth={1.75} /> {t('zip.download')}
          </a>
        )}
      </div>
      {result && <RvcConversionImport key={result.job_id} jobId={result.job_id} />}
    </div>
  )
}
