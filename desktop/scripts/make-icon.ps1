# 生成桌面应用图标（1024x1024 PNG）。electron-builder 会自动转换成 Windows .ico。
# 用代码画而不是找图，保证没有水印、边缘清晰。
Add-Type -AssemblyName System.Drawing

$out = Join-Path $PSScriptRoot "..\build\icon.png"
$size = 1024

function New-RoundedPath {
  param([single]$x, [single]$y, [single]$w, [single]$h, [single]$r)
  $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
  $d = $r * 2
  $path.AddArc($x, $y, $d, $d, 180, 90)
  $path.AddArc($x + $w - $d, $y, $d, $d, 270, 90)
  $path.AddArc($x + $w - $d, $y + $h - $d, $d, $d, 0, 90)
  $path.AddArc($x, $y + $h - $d, $d, $d, 90, 90)
  $path.CloseFigure()
  return $path
}

$bmp = [System.Drawing.Bitmap]::new($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)

$bgPath = New-RoundedPath 80 80 864 864 200
$bgBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
  [System.Drawing.Rectangle]::new(80, 80, 864, 864),
  [System.Drawing.Color]::FromArgb(255, 56, 189, 248),
  [System.Drawing.Color]::FromArgb(255, 67, 56, 202),
  45
)
$g.FillPath($bgBrush, $bgPath)

$white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)

# 向上的箭头
$arrow = [System.Drawing.Drawing2D.GraphicsPath]::new()
$arrow.AddPolygon([System.Drawing.PointF[]]@(
    [System.Drawing.PointF]::new(512, 268),
    [System.Drawing.PointF]::new(414, 392),
    [System.Drawing.PointF]::new(610, 392)
  ))
$arrow.AddRectangle([System.Drawing.RectangleF]::new(482, 392, 60, 100))
$g.FillPath($white, $arrow)

# 三条数据层
foreach ($y in 520, 626, 732) {
  $bar = New-RoundedPath 300 $y 424 62 31
  $g.FillPath($white, $bar)
  $bar.Dispose()
}

$g.Dispose()
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

Write-Host "图标已生成：$out"
