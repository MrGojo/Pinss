import { useMemo, useState } from "react";
import axios from "axios";
import { Download, FileSpreadsheet, ImagePlus, Moon, Sparkles, Sun, WandSparkles, Images } from "lucide-react";
import { Toaster, toast } from "sonner";
import "@/App.css";
import PinCard from "@/components/PinCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

const textPositionOptions = ["top", "center", "bottom"];
const batchSizeOptions = ["50", "100"];

function App() {
  const [excelFile, setExcelFile] = useState(null);
  const [wordFile, setWordFile] = useState(null);
  const [templateFile, setTemplateFile] = useState(null);
  const [mode, setMode] = useState("ai");
  const [customImageFiles, setCustomImageFiles] = useState([]);
  const [imageLinks, setImageLinks] = useState("");
  const [textPosition, setTextPosition] = useState("center");
  const [batchSize, setBatchSize] = useState("50");
  const [progressValue, setProgressValue] = useState(0);
  const [isGenerating, setIsGenerating] = useState(false);
  const [pins, setPins] = useState([]);
  const [sessionId, setSessionId] = useState("");
  const [isDarkMode, setIsDarkMode] = useState(false);
  const [showSwitchModal, setShowSwitchModal] = useState(false);
  const [switchModalMessage, setSwitchModalMessage] = useState("");

  const generatedCount = useMemo(() => pins.length, [pins]);

  const getFilenameFromHeaders = (headers, fallback) => {
    const disposition = headers?.["content-disposition"] || "";
    const standardMatch = disposition.match(/filename="?([^";]+)"?/i);
    return standardMatch?.[1] || fallback;
  };

  const downloadBlobFromApi = async (url, fallbackName) => {
    const response = await axios.get(url, { responseType: "blob" });
    const filename = getFilenameFromHeaders(response.headers, fallbackName);
    const objectUrl = window.URL.createObjectURL(new Blob([response.data]));
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.URL.revokeObjectURL(objectUrl);
  };

  const toggleTheme = () => {
    const updated = !isDarkMode;
    setIsDarkMode(updated);
    document.documentElement.classList.toggle("dark", updated);
  };

  const handleGenerate = async () => {
    if (!excelFile) {
      toast.error("Please upload your Excel or CSV metadata file first.");
      return;
    }

    if (mode === "custom" && !templateFile && customImageFiles.length === 0 && !imageLinks.trim()) {
      toast.error("Custom mode needs uploaded images, image links, or a template.");
      return;
    }

    let generationSucceeded = false;
    let progressTimer = null;
    try {
      setIsGenerating(true);
      setProgressValue(5);
      progressTimer = setInterval(() => {
        setProgressValue((current) => (current < 92 ? current + 6 : current));
      }, 350);

      const formData = new FormData();
      formData.append("data_file", excelFile);
      if (templateFile) {
        formData.append("template_image", templateFile);
      }
      if (wordFile) {
        formData.append("quotes_file", wordFile);
      }

      customImageFiles.forEach((file) => {
        formData.append("custom_images", file);
      });

      formData.append("mode", mode);
      formData.append("image_links", imageLinks);
      formData.append("mapping_strategy", "pin_name_match_then_sequential");
      formData.append("template_text_position", textPosition);
      formData.append("max_pins", batchSize);

      const response = await axios.post(`${API}/pins/generate`, formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      });

      clearInterval(progressTimer);
      setProgressValue(100);
      setPins(response.data.pins || []);
      setSessionId(response.data.session_id || "");
      generationSucceeded = true;
      toast.success(`Generated ${response.data.total_generated} Pinterest pins successfully.`);

      if (response.data.auto_switched) {
        const switchMessage = response.data.switch_message || "AI quota issue detected. Switched to Custom mode.";
        setMode(response.data.mode_used || "custom");
        setSwitchModalMessage(switchMessage);
        setShowSwitchModal(true);
        toast.warning("AI quota issue detected. Switched to Custom mode automatically.");
      }

      if (response.data.skipped_rows > 0) {
        toast.warning(`Skipped ${response.data.skipped_rows} row(s) missing PIN NAME or Quote.`);
      }
    } catch (error) {
      const message = error?.response?.data?.detail || "Pin generation failed. Please check your file format.";
      setProgressValue(0);
      toast.error(message);

       if (mode === "ai" && message.toLowerCase().includes("quota")) {
        setSwitchModalMessage(
          `${message} Upload custom images/links and retry so we can auto-switch instantly.`
        );
        setShowSwitchModal(true);
      }
    } finally {
      if (progressTimer) {
        clearInterval(progressTimer);
      }
      setIsGenerating(false);
      if (!generationSucceeded) {
        setPins([]);
        setSessionId("");
      }
    }
  };

  const handleDownloadSingle = async (pin) => {
    try {
      await downloadBlobFromApi(`${API}/pins/download/${pin.pin_id}`, pin.filename || `${pin.pin_id}.png`);
      toast.success(`Downloaded ${pin.filename || "pin"}`);
    } catch (error) {
      toast.error("Single download failed. Please try again.");
    }
  };

  const handleDownloadAll = async () => {
    if (!sessionId) {
      toast.error("Generate pins first to enable ZIP download.");
      return;
    }
    try {
      await downloadBlobFromApi(`${API}/pins/download-all/${sessionId}`, `${sessionId}-pins.zip`);
      toast.success("ZIP download started.");
    } catch (error) {
      toast.error("ZIP download failed. Please try again.");
    }
  };

  const handleExportMetadata = async (exportFormat) => {
    if (!sessionId) {
      toast.error("Generate pins first to export metadata.");
      return;
    }
    try {
      await downloadBlobFromApi(
        `${API}/pins/export/${sessionId}?export_format=${exportFormat}`,
        `${sessionId}-metadata.${exportFormat}`
      );
      toast.success(`Metadata ${exportFormat.toUpperCase()} downloaded.`);
    } catch (error) {
      toast.error(`Metadata ${exportFormat.toUpperCase()} download failed.`);
    }
  };

  return (
    <div
      className="min-h-screen bg-gradient-to-br from-red-50 via-white to-slate-50 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950"
      data-testid="pin-generator-app"
    >
      <Toaster richColors position="top-right" />
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-8 px-4 py-10 lg:px-8">
        <Dialog open={showSwitchModal} onOpenChange={setShowSwitchModal}>
          <DialogContent data-testid="quota-switch-modal">
            <DialogHeader>
              <DialogTitle data-testid="quota-switch-modal-title">Generation Mode Auto-Switched</DialogTitle>
              <DialogDescription data-testid="quota-switch-modal-message">
                {switchModalMessage}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button onClick={() => setShowSwitchModal(false)} data-testid="quota-switch-modal-close-button">
                Got it
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <header className="rounded-3xl border border-red-100 bg-white/80 p-6 shadow-[0_15px_40px_rgba(239,68,68,0.08)] backdrop-blur dark:border-slate-700 dark:bg-slate-900/80 dark:shadow-[0_15px_40px_rgba(2,6,23,0.45)]">
          <div className="flex flex-wrap items-start justify-between gap-5">
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-red-600 dark:text-red-400" data-testid="header-kicker-text">
                Pinterest Affiliate Workflow
              </p>
              <h1 className="heading-font text-4xl font-bold leading-tight text-slate-900 sm:text-5xl lg:text-6xl dark:text-slate-100" data-testid="app-main-heading">
                Bulk Pin Creator Studio
              </h1>
              <p className="max-w-2xl text-sm text-slate-600 sm:text-base dark:text-slate-300" data-testid="app-subheading-text">
                Dual mode system: AI-generated backgrounds or custom image bulk mode, with 50/100 batch output and full metadata export.
              </p>
            </div>
            <Button
              onClick={toggleTheme}
              variant="outline"
              className="rounded-full border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
              data-testid="theme-toggle-button"
            >
              {isDarkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              {isDarkMode ? "Light" : "Dark"} Mode
            </Button>
          </div>
        </header>

        <section className="grid gap-8 lg:grid-cols-[320px_1fr]" data-testid="dashboard-main-grid">
          <Card className="h-fit rounded-3xl border border-slate-200 bg-white/90 shadow-[0_8px_32px_rgba(15,23,42,0.1)] dark:border-slate-700 dark:bg-slate-900/90">
            <CardHeader>
              <CardTitle className="heading-font text-2xl text-slate-900 dark:text-slate-100" data-testid="upload-panel-title">
                Upload & Generate
              </CardTitle>
              <CardDescription data-testid="upload-panel-description">
                Pick AI mode or Custom mode, then upload metadata and assets.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-2">
                <Label data-testid="mode-selector-label">Generation mode</Label>
                <div className="grid grid-cols-2 gap-2">
                  <Button
                    type="button"
                    variant={mode === "ai" ? "default" : "outline"}
                    className={mode === "ai" ? "rounded-full bg-[#E60023] text-white" : "rounded-full"}
                    onClick={() => setMode("ai")}
                    data-testid="mode-ai-button"
                  >
                    <WandSparkles className="h-4 w-4" />
                    AI Pins
                  </Button>
                  <Button
                    type="button"
                    variant={mode === "custom" ? "default" : "outline"}
                    className={mode === "custom" ? "rounded-full bg-[#E60023] text-white" : "rounded-full"}
                    onClick={() => setMode("custom")}
                    data-testid="mode-custom-button"
                  >
                    <Images className="h-4 w-4" />
                    Custom Pins
                  </Button>
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="excel-file" data-testid="excel-upload-label">Metadata Excel / CSV</Label>
                <Input
                  id="excel-file"
                  type="file"
                  accept=".xlsx,.csv"
                  onChange={(event) => setExcelFile(event.target.files?.[0] || null)}
                  data-testid="excel-upload-input"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="word-file" data-testid="word-upload-label">Word quotes file (optional .docx)</Label>
                <Input
                  id="word-file"
                  type="file"
                  accept=".docx"
                  onChange={(event) => setWordFile(event.target.files?.[0] || null)}
                  data-testid="word-upload-input"
                />
              </div>

              {mode === "custom" ? (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="custom-images" data-testid="custom-images-upload-label">Custom images upload</Label>
                    <Input
                      id="custom-images"
                      type="file"
                      accept="image/*"
                      multiple
                      webkitdirectory="true"
                      onChange={(event) => setCustomImageFiles(Array.from(event.target.files || []))}
                      data-testid="custom-images-upload-input"
                    />
                    <p className="text-xs text-slate-500 dark:text-slate-300" data-testid="custom-mapping-hint-text">
                      Mapping uses PIN NAME filename match first, then sequential fallback.
                    </p>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="image-links" data-testid="image-links-label">Image links (optional, one per line)</Label>
                    <Textarea
                      id="image-links"
                      value={imageLinks}
                      onChange={(event) => setImageLinks(event.target.value)}
                      className="min-h-24"
                      placeholder="https://..."
                      data-testid="image-links-textarea"
                    />
                  </div>
                </>
              ) : null}

              <div className="space-y-2">
                <Label htmlFor="template-file" data-testid="template-upload-label">Template image (optional)</Label>
                <Input
                  id="template-file"
                  type="file"
                  accept="image/*"
                  onChange={(event) => setTemplateFile(event.target.files?.[0] || null)}
                  data-testid="template-upload-input"
                />
                {templateFile ? (
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-8 px-0 text-xs text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300"
                    onClick={() => setTemplateFile(null)}
                    data-testid="template-clear-button"
                  >
                    Remove template and use AI backgrounds
                  </Button>
                ) : null}
              </div>

              <div className="space-y-2">
                <Label data-testid="text-position-label">Template quote position</Label>
                <Select value={textPosition} onValueChange={setTextPosition}>
                  <SelectTrigger className="bg-white dark:bg-slate-900" data-testid="text-position-select-trigger">
                    <SelectValue placeholder="Select text position" />
                  </SelectTrigger>
                  <SelectContent>
                    {textPositionOptions.map((option) => (
                      <SelectItem
                        key={option}
                        value={option}
                        data-testid={`text-position-select-option-${option}`}
                      >
                        {option.charAt(0).toUpperCase() + option.slice(1)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label data-testid="batch-size-label">Batch size</Label>
                <Select value={batchSize} onValueChange={setBatchSize}>
                  <SelectTrigger className="bg-white dark:bg-slate-900" data-testid="batch-size-select-trigger">
                    <SelectValue placeholder="Select batch size" />
                  </SelectTrigger>
                  <SelectContent>
                    {batchSizeOptions.map((option) => (
                      <SelectItem
                        key={option}
                        value={option}
                        data-testid={`batch-size-select-option-${option}`}
                      >
                        {option} pins
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <Button
                onClick={handleGenerate}
                disabled={isGenerating}
                className="h-11 w-full rounded-full bg-[#E60023] text-base font-semibold text-white hover:bg-[#AD081B]"
                data-testid="generate-pins-button"
              >
                {isGenerating ? (
                  <>
                    <Sparkles className="h-4 w-4 animate-pulse" />
                    Generating...
                  </>
                ) : (
                  <>
                    <ImagePlus className="h-4 w-4" />
                    Generate {mode === "ai" ? "AI" : "Custom"} Pins
                  </>
                )}
              </Button>
            </CardContent>
          </Card>

          <div className="space-y-8">
            <Card className="rounded-3xl border border-slate-200 bg-white/90 shadow-[0_8px_32px_rgba(15,23,42,0.08)] dark:border-slate-700 dark:bg-slate-900/90">
              <CardHeader>
                <CardTitle className="heading-font text-2xl text-slate-900 dark:text-slate-100" data-testid="generation-status-title">
                  Generation Status
                </CardTitle>
                <CardDescription data-testid="generation-status-subtitle">
                  Real-time progress for {mode === "ai" ? "AI" : "Custom"} rendering in your selected 50/100 pin batch.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <Progress
                  value={progressValue}
                  className="h-3 bg-slate-100 dark:bg-slate-800 [&>div]:bg-[#E60023]"
                  data-testid="pin-generation-progress-bar"
                />
                <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-700 dark:text-slate-200">
                  <span data-testid="progress-value-text">Progress: {Math.round(progressValue)}%</span>
                  <span data-testid="generated-count-text">Generated Pins: {generatedCount}</span>
                  <span className="truncate" data-testid="generation-session-id-text">
                    Session: {sessionId || "Not started"}
                  </span>
                </div>
                <div className="flex flex-wrap gap-3 pt-2">
                  <Button
                    onClick={handleDownloadAll}
                    variant="outline"
                    className="rounded-full border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    data-testid="download-all-zip-button"
                  >
                    <Download className="h-4 w-4" />
                    Download ZIP
                  </Button>
                  <Button
                    onClick={() => handleExportMetadata("csv")}
                    variant="outline"
                    className="rounded-full border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    data-testid="export-metadata-csv-button"
                  >
                    <FileSpreadsheet className="h-4 w-4" />
                    Export CSV
                  </Button>
                  <Button
                    onClick={() => handleExportMetadata("json")}
                    variant="outline"
                    className="rounded-full border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    data-testid="export-metadata-json-button"
                  >
                    <FileSpreadsheet className="h-4 w-4" />
                    Export JSON
                  </Button>
                </div>
              </CardContent>
            </Card>

            <section className="space-y-5" data-testid="preview-grid-section">
              <div className="flex items-center justify-between gap-4">
                <h2 className="heading-font text-base font-semibold text-slate-900 md:text-lg dark:text-slate-100" data-testid="preview-grid-title">
                  Pin Preview Dashboard
                </h2>
                <p className="text-sm text-slate-600 dark:text-slate-300" data-testid="preview-grid-count-text">
                  {generatedCount} pin(s)
                </p>
              </div>

              {pins.length === 0 ? (
                <div
                  className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-300"
                  data-testid="preview-empty-state"
                >
                  Generate pins to see previews, download images, and export metadata.
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3" data-testid="preview-grid-list">
                  {pins.map((pin) => (
                    <PinCard
                      key={pin.pin_id}
                      pin={pin}
                      backendUrl={BACKEND_URL}
                      onDownload={handleDownloadSingle}
                    />
                  ))}
                </div>
              )}
            </section>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
