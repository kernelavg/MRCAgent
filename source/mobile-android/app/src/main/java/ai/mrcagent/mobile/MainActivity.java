package ai.mrcagent.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ImageView;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.util.Collections;
import java.util.Locale;

public class MainActivity extends Activity {
    private WebView web;
    private String origin = "";
    private static final int BG = Color.rgb(12,14,23);
    private static final int PANEL = Color.rgb(25,29,43);
    private static final int ACCENT = Color.rgb(151,120,244);

    private int dp(int value) { return Math.round(value / (1.0f / getResources().getDisplayMetrics().density)); }
    private GradientDrawable background(int color, int stroke, int radius) {
        GradientDrawable d = new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(radius));
        if (stroke != 0) d.setStroke(dp(1), stroke); return d;
    }
    private TextView text(String value, int size, int color) {
        TextView t = new TextView(this); t.setText(value); t.setTextSize(size); t.setTextColor(color); return t;
    }
    private void add(LinearLayout root, View view, int top) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.topMargin = dp(top); root.addView(view, p);
    }
    @Override public void onCreate(Bundle bundle) { super.onCreate(bundle); getWindow().setStatusBarColor(BG); getWindow().setNavigationBarColor(BG); showConnection(); }
    private void showConnection() {
        if (web != null) { web.stopLoading(); web.destroy(); web = null; }
        LinearLayout outer = new LinearLayout(this); outer.setOrientation(LinearLayout.VERTICAL); outer.setPadding(dp(24),dp(34),dp(24),dp(24)); outer.setBackground(new GradientDrawable(GradientDrawable.Orientation.TR_BL, new int[]{Color.rgb(43,31,74), BG, BG}));
        ImageView logo = new ImageView(this); logo.setImageResource(R.drawable.ic_app);
        LinearLayout.LayoutParams logoParams = new LinearLayout.LayoutParams(dp(74), dp(74)); outer.addView(logo, logoParams);
        TextView brand=text("MRCAgent Mobile",24,Color.WHITE); brand.setTypeface(null,Typeface.BOLD); add(outer,brand,16);
        TextView tag=text("МОБИЛЬНЫЙ ПУЛЬТ",10,ACCENT); tag.setLetterSpacing(.16f); add(outer,tag,12);
        TextView title=text("Ваши агенты\nвсегда рядом.",32,Color.WHITE); title.setTypeface(null,Typeface.BOLD); add(outer,title,42);
        TextView sub=text("Подключите телефон к MRCAgent на компьютере: по Wi‑Fi или через Tailscale.",14,Color.rgb(169,175,197)); add(outer,sub,14);
        LinearLayout card=new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL);card.setPadding(dp(18),dp(18),dp(18),dp(18));card.setBackground(background(PANEL,Color.rgb(54,57,78),18));
        TextView label=text("АДРЕС КОМПЬЮТЕРА",11,Color.rgb(180,164,240));label.setTypeface(null,Typeface.BOLD);card.addView(label);
        EditText address=new EditText(this);address.setSingleLine(true);address.setTextColor(Color.WHITE);address.setHintTextColor(Color.rgb(117,122,145));address.setTextSize(14);address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);address.setPadding(dp(14),dp(12),dp(14),dp(12));address.setBackground(background(BG,Color.rgb(63,67,88),10));address.setHint("http://192.168.1.20:8777");address.setText(getPreferences(MODE_PRIVATE).getString("origin", ""));add(card,address,13);
        TextView tip=text("Укажите IP компьютера и порт. Для Tailscale — IP компьютера в вашей сети Tailscale.",12,Color.rgb(151,159,180));add(card,tip,12);
        Button connect=new Button(this);connect.setText("ПОДКЛЮЧИТЬСЯ  ↗");connect.setTextColor(Color.WHITE);connect.setAllCaps(false);connect.setBackground(background(ACCENT,0,10));add(card,connect,18);
        Button preview=new Button(this); preview.setText("ПОСМОТРЕТЬ ИНТЕРФЕЙС БЕЗ ПК"); preview.setTextColor(Color.rgb(230,216,255)); preview.setAllCaps(false); preview.setBackground(background(Color.rgb(50,40,73),Color.rgb(114,87,157),10)); add(card,preview,10);
        TextView demoHint=text("Откройте тестовые проекты и попробуйте кнопки без подключения.",12,Color.rgb(151,159,180)); add(card,demoHint,10);
        preview.setOnClickListener(v -> open("https://demo.mrcagent.invalid", true));
        add(outer,card,30);
        TextView safety=text("HTTP в обычном Wi‑Fi не защищает пароль. Используйте только доверенную сеть или Tailscale. Никогда не открывайте порт MRCAgent в публичный интернет.",12,Color.rgb(183,174,159)); add(outer,safety,24);
        TextView version=text("MRCAgent Mobile · ваш проект на связи",11,Color.rgb(106,113,134));version.setGravity(Gravity.CENTER);add(outer,version,36);
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.setBackgroundColor(BG); scroll.addView(outer); setContentView(scroll);
        connect.setOnClickListener(v -> {
            String raw=address.getText().toString().trim();
            if (!raw.contains("://")) raw="http://"+raw;
            final String chosen=normalize(raw);
            if (chosen==null) {new AlertDialog.Builder(this).setMessage("Укажите HTTPS-адрес или частный IPv4-адрес локальной сети / Tailscale. Адрес не должен содержать путь, логин или пароль.").setPositiveButton("Понятно",null).show();return;}
            if (chosen.startsWith("http://")) {
                new AlertDialog.Builder(this).setTitle("Незашифрованный HTTP")
                    .setMessage("В обычной сети пароль и данные видны сети. Используйте доверенный Wi‑Fi или подключение через Tailscale. Продолжить?")
                    .setNegativeButton("Отмена",null).setPositiveButton("Продолжить",(d,w)->open(chosen, false)).show();
            } else open(chosen, false);
        });
    }
    private String normalize(String raw) {
        try {
            Uri u=Uri.parse(raw);String scheme=u.getScheme();String host=u.getHost();
            if (scheme==null || host==null || u.getUserInfo()!=null || u.getQuery()!=null || u.getFragment()!=null) return null;
            if (u.getPath()!=null && !u.getPath().isEmpty() && !u.getPath().equals("/")) return null;
            scheme=scheme.toLowerCase(Locale.ROOT);host=host.toLowerCase(Locale.ROOT);
            if (!scheme.equals("https") && !scheme.equals("http")) return null;
            if (scheme.equals("http") && !privateAddress(host)) return null;
            int port=u.getPort();if(port==0 || port>65535)return null;
            return scheme+"://"+host+(port<0?"":":"+port);
        } catch (Exception e) {return null;}
    }
    private boolean privateAddress(String host) {
        String[] parts=host.split("\\.");if(parts.length!=4)return false;
        try {int a=Integer.parseInt(parts[0]);int b=Integer.parseInt(parts[1]);
            for(String p:parts){int n=Integer.parseInt(p);if(n<0 || n>255)return false;}
            return a==10 || (a==192 && b==168) || (a==172 && b>=16 && b<=31) || (a==100 && b>=64 && b<=127);
        } catch(NumberFormatException e){return false;}
    }
    private boolean sameOrigin(Uri u) {
        if(u==null || u.getScheme()==null || u.getHost()==null || u.getUserInfo()!=null)return false;
        Uri base=Uri.parse(origin);
        int port=u.getPort()<0?("https".equalsIgnoreCase(u.getScheme())?443:80):u.getPort();
        int bp=base.getPort()<0?("https".equalsIgnoreCase(base.getScheme())?443:80):base.getPort();
        return base.getScheme().equalsIgnoreCase(u.getScheme()) && base.getHost().equalsIgnoreCase(u.getHost()) && port==bp;
    }
    private WebResourceResponse demoResource(Uri uri, String method) {
        try {
            String path=uri.getPath();
            if (sameOrigin(uri) && "GET".equals(method) && path != null &&
                (path.equals("/static/mobile.html") || path.equals("/static/mobile.css") || path.equals("/static/mobile.js") || path.equals("/static/demo.js"))) {
                String type=path.endsWith(".html") ? "text/html" : path.endsWith(".css") ? "text/css" : "application/javascript";
                InputStream stream=getAssets().open(path.substring(1));
                return new WebResourceResponse(type,"UTF-8",200,"OK",Collections.singletonMap("Cache-Control","no-store"),stream);
            }
        } catch (Exception ignored) { }
        return new WebResourceResponse("text/plain","UTF-8",404,"Not Found",Collections.emptyMap(),new ByteArrayInputStream(new byte[0]));
    }
    private void open(String chosen, boolean demo) {
        origin=chosen;if(!demo)getPreferences(MODE_PRIVATE).edit().putString("origin",chosen).apply();
        LinearLayout container=new LinearLayout(this);container.setOrientation(LinearLayout.VERTICAL);container.setBackgroundColor(BG);
        TextView nav=text(demo ? "‹  Выйти из демо" : "‹  Подключение",14,Color.rgb(199,185,255));nav.setPadding(dp(18),dp(10),dp(18),dp(10));nav.setOnClickListener(v->showConnection());container.addView(nav);
        web=new WebView(this);web.setBackgroundColor(BG);WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);settings.setJavaScriptCanOpenWindowsAutomatically(false);settings.setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
        web.setWebViewClient(new WebViewClient(){
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request){
                if(demo)return demoResource(request.getUrl(),request.getMethod());
                return null;
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, String url){
                if(demo)return demoResource(Uri.parse(url),"GET");
                return null;
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){return !sameOrigin(request.getUrl());}
            @Override public boolean shouldOverrideUrlLoading(WebView view,String url){return !sameOrigin(Uri.parse(url));}
        });
        web.setDownloadListener((url,agent,disposition,mime,length)->{
            Uri target=Uri.parse(url);
            if (demo || !sameOrigin(target) || target.getPath()==null || !target.getPath().startsWith("/download/")) return;
            new AlertDialog.Builder(this).setTitle("Скачать файл").setMessage("Открыть временную ссылку на файл в браузере телефона?").setNegativeButton("Отмена",null).setPositiveButton("Открыть",(d,w)->{try{startActivity(new Intent(Intent.ACTION_VIEW,target));}catch(Exception ignored){new AlertDialog.Builder(this).setMessage("Нет приложения для открытия ссылки").setPositiveButton("ОК",null).show();}}).show();
        });
        container.addView(web,new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,0,1));setContentView(container);web.loadUrl(origin+"/static/mobile.html");
    }
    @Override public void onBackPressed(){if(web!=null && web.canGoBack())web.goBack();else if(web!=null)showConnection();else super.onBackPressed();}
    @Override protected void onDestroy(){if(web!=null){web.stopLoading();web.destroy();web=null;}super.onDestroy();}
}
