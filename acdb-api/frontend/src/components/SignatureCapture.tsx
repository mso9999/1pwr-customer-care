import { useCallback, useEffect, useRef, useState } from 'react';

interface SignatureCaptureProps {
  onCapture: (b64: string) => void;
  helperText?: string;
}

const JPEG_ACCEPT = '.jpg,.jpeg,image/jpeg';
const MAX_UPLOAD_WIDTH = 1200;
const MAX_UPLOAD_HEIGHT = 480;

function isJpegFile(file: File): boolean {
  return file.type === 'image/jpeg' || /\.jpe?g$/i.test(file.name);
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        reject(new Error('Could not read the JPEG file'));
        return;
      }
      resolve(reader.result);
    };
    reader.onerror = () => reject(new Error('Could not read the JPEG file'));
    reader.readAsDataURL(file);
  });
}

function normalizeUploadedSignature(dataUrl: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const image = new Image();

    image.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = MAX_UPLOAD_WIDTH;
      canvas.height = MAX_UPLOAD_HEIGHT;

      const ctx = canvas.getContext('2d');
      if (!ctx) {
        reject(new Error('Could not process the JPEG file'));
        return;
      }

      const scale = Math.min(
        MAX_UPLOAD_WIDTH / image.width,
        MAX_UPLOAD_HEIGHT / image.height,
        1,
      );
      const width = Math.max(1, Math.round(image.width * scale));
      const height = Math.max(1, Math.round(image.height * scale));
      const x = Math.round((canvas.width - width) / 2);
      const y = Math.round((canvas.height - height) / 2);

      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, x, y, width, height);

      const normalizedUrl = canvas.toDataURL('image/jpeg', 0.85);
      const b64 = normalizedUrl.split(',')[1];
      if (!b64) {
        reject(new Error('Could not process the JPEG file'));
        return;
      }
      resolve(b64);
    };

    image.onerror = () => reject(new Error('Could not process the JPEG file'));
    image.src = dataUrl;
  });
}

export default function SignatureCapture({
  onCapture,
  helperText = 'Sign above using your finger or stylus, or upload a JPEG signature image.',
}: SignatureCaptureProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDrawing, setIsDrawing] = useState(false);
  const [hasContent, setHasContent] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [processingUpload, setProcessingUpload] = useState(false);

  const getPos = useCallback((e: React.TouchEvent | React.MouseEvent) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };

    const rect = canvas.getBoundingClientRect();
    if ('touches' in e) {
      return { x: e.touches[0].clientX - rect.left, y: e.touches[0].clientY - rect.top };
    }

    return {
      x: (e as React.MouseEvent).clientX - rect.left,
      y: (e as React.MouseEvent).clientY - rect.top,
    };
  }, []);

  const startDraw = useCallback((e: React.TouchEvent | React.MouseEvent) => {
    e.preventDefault();
    setUploadError('');

    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx) return;

    const pos = getPos(e);
    ctx.beginPath();
    ctx.moveTo(pos.x, pos.y);
    setIsDrawing(true);
  }, [getPos]);

  const draw = useCallback((e: React.TouchEvent | React.MouseEvent) => {
    e.preventDefault();
    if (!isDrawing) return;

    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx) return;

    const pos = getPos(e);
    ctx.lineTo(pos.x, pos.y);
    ctx.strokeStyle = '#000';
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke();
    setHasContent(true);
  }, [isDrawing, getPos]);

  const endDraw = useCallback(() => {
    setIsDrawing(false);
  }, []);

  const clearCanvas = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx || !canvas) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasContent(false);
    setUploadError('');
  };

  const acceptSignature = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
    const b64 = dataUrl.split(',')[1];
    if (!b64) return;
    onCapture(b64);
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleUploadChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';

    if (!file) return;

    if (!isJpegFile(file)) {
      setUploadError('Please upload a JPEG signature image (.jpg or .jpeg).');
      return;
    }

    setProcessingUpload(true);
    setUploadError('');

    try {
      const dataUrl = await readFileAsDataUrl(file);
      const normalizedB64 = await normalizeUploadedSignature(dataUrl);
      onCapture(normalizedB64);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : 'Could not process the JPEG file');
    } finally {
      setProcessingUpload(false);
    }
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const parent = canvas.parentElement;
    if (!parent) return;

    canvas.width = parent.clientWidth;
    canvas.height = Math.min(200, parent.clientWidth * 0.4);
  }, []);

  return (
    <div className="space-y-3">
      <div className="border-2 border-gray-300 rounded-xl overflow-hidden bg-white touch-none">
        <canvas
          ref={canvasRef}
          className="w-full cursor-crosshair"
          onMouseDown={startDraw}
          onMouseMove={draw}
          onMouseUp={endDraw}
          onMouseLeave={endDraw}
          onTouchStart={startDraw}
          onTouchMove={draw}
          onTouchEnd={endDraw}
        />
      </div>

      <p className="text-xs text-gray-400 text-center">{helperText}</p>

      <div className="flex gap-3">
        <button
          type="button"
          onClick={clearCanvas}
          className="flex-1 py-3 bg-gray-100 text-gray-600 rounded-xl font-medium text-sm hover:bg-gray-200 active:bg-gray-300 transition"
        >
          Clear
        </button>

        <button
          type="button"
          onClick={acceptSignature}
          disabled={!hasContent || processingUpload}
          className="flex-1 py-3 bg-green-600 text-white rounded-xl font-semibold text-sm hover:bg-green-700 active:bg-green-800 disabled:opacity-40 transition"
        >
          Accept Signature
        </button>
      </div>

      <div className="flex items-center gap-3 py-1">
        <div className="h-px flex-1 bg-gray-200" />
        <span className="text-[11px] font-medium uppercase tracking-wide text-gray-400">or</span>
        <div className="h-px flex-1 bg-gray-200" />
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept={JPEG_ACCEPT}
        className="hidden"
        onChange={handleUploadChange}
      />

      <button
        type="button"
        onClick={handleUploadClick}
        disabled={processingUpload}
        className="w-full py-3 bg-blue-50 text-blue-700 border border-blue-200 rounded-xl font-medium text-sm hover:bg-blue-100 active:bg-blue-200 disabled:opacity-50 transition"
      >
        {processingUpload ? 'Processing JPEG...' : 'Upload JPEG Signature'}
      </button>

      <p className="text-xs text-gray-400 text-center">
        Use a cropped photo or scan of the customer&apos;s handwritten signature.
      </p>

      {uploadError && (
        <p className="text-xs text-red-500 text-center">{uploadError}</p>
      )}
    </div>
  );
}
