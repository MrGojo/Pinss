import { Download } from "lucide-react";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function PinCard({ pin, backendUrl, onDownload }) {
  const imageSource = pin.image_url?.startsWith("http")
    ? pin.image_url
    : `${backendUrl}${pin.image_url}`;

  const handleDownload = () => {
    onDownload(pin);
  };

  return (
    <Card
      className="group overflow-hidden rounded-2xl border-slate-200 bg-white/90 shadow-[0_6px_25px_rgba(15,23,42,0.08)] transition-transform duration-300 hover:-translate-y-1 dark:border-slate-700 dark:bg-slate-900/90"
      data-testid={`pin-card-${pin.pin_id}`}
    >
      <CardContent className="p-0">
        <div className="relative aspect-[2/3] w-full overflow-hidden" data-testid={`pin-preview-image-wrap-${pin.pin_id}`}>
          <img
            src={imageSource}
            alt={pin.quote}
            className="h-full w-full object-cover object-center"
            loading="lazy"
            data-testid={`pin-preview-image-${pin.pin_id}`}
          />
        </div>
      </CardContent>
      <CardFooter className="flex flex-col items-stretch gap-2 border-t border-slate-100 p-4 pt-3 dark:border-slate-700">
        <p
          className="line-clamp-1 text-xs font-bold uppercase tracking-wide text-slate-500 dark:text-slate-300"
          data-testid={`pin-name-text-${pin.pin_id}`}
          title={pin.pin_name || pin.filename}
        >
          {pin.pin_name || pin.filename}
        </p>
        {pin.pin_title_2nd_line ? (
          <p
            className="line-clamp-2 text-sm font-bold leading-snug text-slate-700 dark:text-slate-200 sm:text-base"
            data-testid={`pin-title-2nd-${pin.pin_id}`}
            title={pin.pin_title_2nd_line}
          >
            {pin.pin_title_2nd_line}
          </p>
        ) : null}
        <p
          className="line-clamp-3 text-base font-semibold leading-snug text-slate-800 dark:text-slate-100 sm:text-lg mt-0.5 mb-1"
          data-testid={`pin-quote-text-${pin.pin_id}`}
          title={pin.quote}
        >
          {pin.quote}
        </p>
        <Button
          onClick={handleDownload}
          className="h-9 w-full rounded-full bg-[#E60023] text-white hover:bg-[#AD081B]"
          data-testid={`pin-download-button-${pin.pin_id}`}
        >
          <Download className="h-4 w-4" />
          Download
        </Button>
      </CardFooter>
    </Card>
  );
}
